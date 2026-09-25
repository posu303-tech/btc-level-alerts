"""Tests for the BTC-USD level monitor.

All tests are offline: network fetch functions are monkeypatched and the
current UTC time is faked via ``monitor.now_utc`` so level math and the setup
state machines are deterministic.
"""

import json

import pytest

import monitor


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_candle(open_time_ms, o, h, l, c, vol=1000.0, quote=None):
    """Build one candle dict in monitor's internal shape (Binance-like)."""
    span = 4 * 60 * 60 * 1000  # 4h
    quote = quote if quote is not None else vol * c
    return {
        "open_time": open_time_ms,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": vol,
        "close_time": open_time_ms + span - 1,
        "quote_volume": quote,
        "vwap": quote / vol if vol else c,
    }


def make_1d_series(n_days=60, base_open_ms=1700000000000, close=64000.0):
    """Daily candles ending on the most recent *closed* day before `now`."""
    day_ms = 86400000
    candles = []
    for i in range(n_days):
        t = base_open_ms + i * day_ms
        o = close + (i % 5) * 20 - 40
        candles.append(
            make_candle(t, o, o + 1500, o - 1200, o + 300, 5000.0)
        )
    return candles


def make_4h_series(session_start, n=40, price=64000.0, vol=2000.0):
    """4h candles covering `session_start` and before it (all closed)."""
    span = 4 * 60 * 60 * 1000
    out = []
    for i in range(n):
        t = session_start - (n - i) * span
        o = price + (i % 3) * 10 - 10
        out.append(make_candle(t, o, o + 200, o - 180, o - 40, vol))
    return out


class FakeClock:
    """Deterministic UTC clock with millisecond precision."""

    def __init__(self, iso="2026-09-11T12:00:00"):
        from datetime import datetime, timezone
        self.dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)

    def now(self):
        return self.dt


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def patched_time(monkeypatch, clock):
    monkeypatch.setattr(monitor, "now_utc", clock.now)
    return clock


@pytest.fixture
def config():
    return json.loads(json.dumps(monitor.DEFAULT_CONFIG))


# --------------------------------------------------------------------------- #
# Candles / conversion
# --------------------------------------------------------------------------- #

class TestCandles:
    def test_binance_to_candles(self):
        raw = [[1700000000000, "50000", "51000", "49000", "50500",
                "100", 1700000000000 + 1, "5050000"]]
        out = monitor.to_candles(raw)[0]
        assert out["open_time"] == 1700000000000
        assert out["close"] == 50500.0
        assert out["volume"] == 100.0
        assert out["quote_volume"] == 5050000.0
        assert out["vwap"] == pytest.approx(50500.0)

    def test_kraken_to_candles(self):
        # [time, open, high, low, close, vwap, volume, count]
        raw = [[1700000000, "50000", "51000", "49000", "50500",
                "50000", "100", "3"]]
        out = monitor.to_candles_kraken(raw, "1d")[0]
        assert out["open_time"] == 1700000000000
        assert out["close"] == 50500.0
        assert out["quote_volume"] == pytest.approx(50000.0 * 100.0)
        assert out["vwap"] == pytest.approx(50000.0)

    def test_kucoin_to_candles_sorted(self):
        # newest-first input, ascending output
        raw = [
            [1700001000, "60000", "60100", "59900", "60050", "50", "3000000"],
            [1700000000, "59900", "60000", "59800", "59950", "40", "2398000"],
        ]
        out = monitor.to_candles_kucoin(raw, "1d")
        assert [c["open_time"] for c in out] == sorted(c["open_time"] for c in out)
        assert out[0]["open_time"] == 1700000000000

    def test_closed_candles_filters_pending(self, patched_time):
        now_ms = monitor.utc_ms(patched_time.now())
        closed = make_candle(now_ms - 3600000, 1, 2, 0, 1.5)
        open_ = make_candle(now_ms + 1000, 1, 2, 0, 1.5)
        closed["close_time"] = now_ms - 1  # ensure closed
        open_["close_time"] = now_ms + 5000  # in future
        out = monitor.closed_candles([closed, open_])
        assert out == [closed]


# --------------------------------------------------------------------------- #
# Level math
# --------------------------------------------------------------------------- #

class TestLevels:
    def test_floor_pivots(self):
        # prev day H=101, L=99, C=100 -> P=100, R1=101, S1=99, R2=102, S2=98
        k = make_candle(0, 99, 101, 99, 100, 1000.0)
        # ensure single prev candle
        levels, prev = monitor.compute_levels([k], [], {})
        assert levels["PIVOT"] == pytest.approx(100.0)
        assert levels["R1"] == pytest.approx(101.0)
        assert levels["S1"] == pytest.approx(99.0)
        assert levels["R2"] == pytest.approx(102.0)
        assert levels["S2"] == pytest.approx(98.0)
        assert prev["close"] == 100.0

    def test_session_vwap_anchored_at_utc_midnight(self, patched_time):
        # patched_time pins monitor.now_utc to 2026-09-11T12:00 UTC.
        day_start_ms = monitor.utc_ms(
            __import__("datetime").datetime(
                2026, 9, 11, tzinfo=__import__("datetime").timezone.utc
            )
        )
        kl_4h = [
            make_candle(day_start_ms, 50000, 51000, 49000, 50500, 10.0, 505000.0),
            make_candle(day_start_ms + 14400000, 50500, 51500, 50000, 51000, 20.0, 1020000.0),
        ]
        # prev day for pivots
        kl_1d = [make_candle(day_start_ms - 86400000, 48000, 49000, 47000, 48500, 1000.0)]
        levels, _ = monitor.compute_levels(kl_1d, kl_4h, {})
        # VWAP = (505*10 + 510*20)/30 = 508.33
        assert levels["VWAP"] == pytest.approx((50500.0 * 10 + 51000.0 * 20) / 30.0)
        assert levels["SESSION_HIGH"] == pytest.approx(51500.0)
        assert levels["SESSION_LOW"] == pytest.approx(49000.0)

    def test_fib_levels_merged(self):
        k = make_candle(0, 1, 2, 0, 1)
        levels, _ = monitor.compute_levels([k], [], {"FIB618": 65593, "VAH": 65500})
        assert levels["FIB618"] == 65593
        assert levels["VAH"] == 65500

    def test_resolve(self):
        levels = {"PIVOT": 100.0}
        assert monitor.resolve(123, levels) == 123.0
        assert monitor.resolve("PIVOT", levels) == 100.0
        with pytest.raises(KeyError):
            monitor.resolve("NOPE", levels)

    def test_atr_14(self, patched_time):
        # 15 candles of same TR -> ATR == TR
        now_ms = monitor.utc_ms(patched_time.now())
        candles = []
        for i in range(15):
            t = now_ms - (15 - i) * 14400000
            candles.append(make_candle(t, 100, 110, 90, 105))
        assert monitor.atr_14(candles) == pytest.approx(20.0)

    def test_atr_14_insufficient(self):
        assert monitor.atr_14([make_candle(0, 1, 2, 0, 1)]) is None

    def test_sma_and_avg_volume(self, patched_time):
        closes = [float(i) for i in range(20)]
        assert monitor.sma(closes, 5) == pytest.approx(sum(closes[-5:]) / 5)
        assert monitor.sma(closes, 99) is None
        candles = [make_candle(i * 1000, 1, 2, 0, 1, vol=float(i + 1)) for i in range(20)]
        assert monitor.avg_volume(candles, 20) == pytest.approx(sum(range(1, 21)) / 20.0)


# --------------------------------------------------------------------------- #
# State persistence
# --------------------------------------------------------------------------- #

class TestStateIO:
    def test_load_state_missing(self, tmp_path):
        s = monitor.load_state(str(tmp_path / "nope.json"))
        assert s == {"setups": {}, "day": None}

    def test_load_state_corrupt(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{not json")
        s = monitor.load_state(str(p))
        assert s == {"setups": {}, "day": None}

    def test_roundtrip(self, tmp_path):
        p = tmp_path / "state.json"
        monitor.save_state(str(p), {"day": 20706, "setups": {"L1": {"state": "idle"}}})
        s = monitor.load_state(str(p))
        assert s["day"] == 20706
        assert s["setups"]["L1"]["state"] == "idle"


# --------------------------------------------------------------------------- #
# Setup evaluators — state machines
# --------------------------------------------------------------------------- #

class TestEvalZone:
    def test_idle_to_armed_to_triggered_long(self, patched_time):
        clock = patched_time
        st = {"state": "idle"}
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "atr4h": 400}
        levels = {"PIVOT": 64100.0, "S1": 63000.0, "PRIOR_HIGH": 64600.0, "R1": 65200.0}
        setup = {
            "id": "L1", "name": "L1", "side": "long", "type": "zone",
            "entry_min": 63900.0, "entry_max": "PIVOT", "hold_minutes": 30,
            "stop": "S1", "t1": "PRIOR_HIGH", "t2": "R1",
        }
        # price in zone -> armed
        al = monitor.eval_zone(monitor.DEFAULT_CONFIG, setup, st, 64000, levels, ctx)
        assert al and any("ARMED" in a for a in al)
        assert st["state"] == "armed"
        # advance 30+ min -> triggered
        ctx["now_ms"] = ctx["now_ms"] + 40 * 60000
        al = monitor.eval_zone(monitor.DEFAULT_CONFIG, setup, st, 64000, levels, ctx)
        assert any("TRIGGERED" in a for a in al)
        assert st["state"] == "triggered"

    def test_triggered_long_stop_hit(self, patched_time):
        clock = patched_time
        st = {"state": "triggered"}
        levels = {"S1": 63000.0, "PRIOR_HIGH": 64600.0, "R1": 65200.0}
        setup = {
            "id": "L1", "name": "L1", "side": "long", "type": "zone",
            "entry_min": 63900.0, "entry_max": 64100.0,
            "hold_minutes": 30, "stop": "S1", "t1": "PRIOR_HIGH", "t2": "R1",
        }
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "atr4h": 400}
        al = monitor.eval_zone(monitor.DEFAULT_CONFIG, setup, st, 62900, levels, ctx)
        assert al and any("STOP HIT" in a for a in al)
        assert st["state"] == "idle"
        assert st.get("guard") == 62900.0  # re-entry guard armed

    def test_triggered_long_t1_partial_stays_triggered(self, patched_time):
        clock = patched_time
        st = {"state": "triggered"}
        levels = {"S1": 63000.0, "PRIOR_HIGH": 64600.0, "R1": 65200.0}
        setup = {
            "id": "L1", "name": "L1", "side": "long", "type": "zone",
            "entry_min": 63900.0, "entry_max": 64100.0,
            "hold_minutes": 30, "stop": "S1", "t1": "PRIOR_HIGH", "t2": "R1",
        }
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "atr4h": 400}
        al = monitor.eval_zone(monitor.DEFAULT_CONFIG, setup, st, 64700, levels, ctx)
        assert al and any("T1 HIT" in a for a in al)
        assert st["state"] == "triggered"


class TestEvalCloseBreak:
    def _setup(self):
        return {
            "id": "L2", "name": "L2", "side": "long", "type": "close_break",
            "level": "PRIOR_HIGH", "stop": "VWAP", "t1": "R1", "t2": "VAH",
            "volume_threshold_btc": 1700,
        }

    def test_breakout_trigger_and_exit_check_runs_next_cycle(self, patched_time):
        """Regression: exit check must run the cycle AFTER a new candle fires."""
        clock = patched_time
        # Two distinct 4h candles with the same close_time handling in the caller.
        c1 = make_candle(monitor.utc_ms(clock.now()) - 2 * 14400000, 64000, 64200, 63800, 64100, 3000.0)
        # c1 closed above PRIOR_HIGH 64000 -> trigger
        levels = {"PRIOR_HIGH": 64000.0, "VWAP": 63500.0, "R1": 65200.0, "VAH": 65500.0}
        setup = self._setup()
        st = {"state": "idle"}
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "avg_vol4h": 2000, "provider": "binance"}
        al = monitor.eval_close_break(monitor.DEFAULT_CONFIG, setup, st, 64100, levels, ctx, c1)
        assert any("TRIGGERED" in a for a in al)
        assert st["state"] == "triggered"

        # New candle arrives (fires the `if` branch), but price has now dropped
        # to stop. Exit must be detected THIS cycle, not skipped.
        c2 = make_candle(monitor.utc_ms(clock.now()) - 14400000, 63000, 63200, 62900, 63100, 3000.0)
        al = monitor.eval_close_break(monitor.DEFAULT_CONFIG, setup, st, 63400, levels, ctx, c2)
        assert any("STOP HIT" in a for a in al)
        assert st["state"] == "idle"

    def test_volume_verdict(self, patched_time):
        clock = patched_time
        c = make_candle(monitor.utc_ms(clock.now()) - 14400000, 64000, 64200, 63800, 64100, 1000.0)
        levels = {"PRIOR_HIGH": 64000.0, "VWAP": 63500.0, "R1": 65200.0, "VAH": 65500.0}
        setup = self._setup()
        st = {"state": "idle"}
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "avg_vol4h": 2000, "provider": "binance"}
        al = monitor.eval_close_break(monitor.DEFAULT_CONFIG, setup, st, 64100, levels, ctx, c)
        assert any("UNCONFIRMED" in a for a in al)


class TestEvalRejection:
    def test_zone_touch_rejection_trigger_short(self, patched_time):
        clock = patched_time
        setup = {
            "id": "S1", "name": "S1", "side": "short", "type": "rejection",
            "zone_min": 65546.0, "zone_max": 65593.0, "zone_tolerance": 50,
            "stop": "R2", "t1": "PRIOR_HIGH", "t2": "PIVOT", "volume_cap_btc": 1000,
        }
        levels = {"R2": 65823.0, "PRIOR_HIGH": 64600.0, "PIVOT": 63964.0}
        st = {"state": "idle"}
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "atr4h": 400}
        # price 65570 inside zone base 65546 +/- 50 (65496..65596); bearish + upper wick
        candle = make_candle(monitor.utc_ms(clock.now()) - 14400000,
                             65600, 65800, 65500, 65550, 800.0)
        al = monitor.eval_rejection(monitor.DEFAULT_CONFIG, setup, st, 65570, levels, ctx, candle)
        assert al and any("TRIGGERED" in a for a in al)
        assert st["state"] == "triggered"

    def test_zone_touch_no_rejection_armed_only(self, patched_time):
        clock = patched_time
        setup = {
            "id": "S1", "name": "S1", "side": "short", "type": "rejection",
            "zone_min": 65546.0, "zone_max": 65593.0, "zone_tolerance": 50,
            "stop": "R2", "t1": "PRIOR_HIGH", "t2": "PIVOT", "volume_cap_btc": 1000,
        }
        levels = {"R2": 65823.0, "PRIOR_HIGH": 64600.0, "PIVOT": 63964.0}
        st = {"state": "idle"}
        ctx = {"now_ms": monitor.utc_ms(clock.now()), "atr4h": 400}
        # bullish candle, small upper wick -> no rejection
        candle = make_candle(monitor.utc_ms(clock.now()) - 14400000,
                             65500, 65600, 65400, 65590, 800.0)
        al = monitor.eval_rejection(monitor.DEFAULT_CONFIG, setup, st, 65560, levels, ctx, candle)
        assert al and any("R2 TEST" in a for a in al)
        assert st["state"] == "idle"


# --------------------------------------------------------------------------- #
# JSON output
# --------------------------------------------------------------------------- #

class TestJsonOut:
    def test_json_sheet_shape(self, monkeypatch, patched_time, capsys):
        day_start_ms = monitor.utc_ms(
            __import__("datetime").datetime(
                2026, 9, 11, tzinfo=__import__("datetime").timezone.utc
            )
        )
        kl_1d = [make_candle(day_start_ms - 86400000, 48000, 49000, 47000, 48500, 1000.0)]
        kl_4h = make_4h_series(day_start_ms, n=20, price=64000.0)
        monkeypatch.setattr(
            monitor, "fetch_market",
            lambda cfg: ("binance", kl_1d, kl_4h, 64000.0),
        )
        state = {"setups": {}, "day": None}
        monitor.run_once(monitor.DEFAULT_CONFIG, state, print_sheet=False, json_out=True)
        out = capsys.readouterr().out
        sheet = json.loads(out)
        assert sheet["price"] == 64000.0
        assert sheet["provider"] == "binance"
        assert "PIVOT" in sheet["levels"]
        assert "previous_day" in sheet


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

class TestMisc:
    def test_fmt(self):
        assert monitor.fmt(64130.0) == "64,130"

    def test_handle_exit_short(self):
        st = {"state": "triggered"}
        al = []
        setup = {"id": "S2", "side": "short"}
        # short: stop hit when price RISES above stop; t2 when it FALLS below
        out = monitor.handle_exit(setup, st, 64000, 63964.0, 62166.0, 62105.0, al)
        assert out == "stop"
        assert st["state"] == "idle"
        assert any("STOP HIT" in a for a in al)

        st = {"state": "triggered"}
        al = []
        out = monitor.handle_exit(setup, st, 62000, 63964.0, 62166.0, 62105.0, al)
        assert out == "t2"
        assert st["state"] == "idle"
        assert any("T2 HIT" in a for a in al)

        # T1 (partial) stays triggered
        st = {"state": "triggered"}
        al = []
        out = monitor.handle_exit(setup, st, 62150, 63964.0, 62166.0, 62105.0, al)
        assert out == "t1"
        assert st["state"] == "triggered"