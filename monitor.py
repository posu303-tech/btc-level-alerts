#!/usr/bin/env python3
"""
BTC-USD level monitor - alerts when the desk's trade setups trigger.

Polls Binance public market data (no API key required), computes session
levels (floor pivots, session VWAP, session high/low, prior-day VWAP, ATR,
volume averages, SMAs) and evaluates the setups defined in config against
the live price and 4h candle closes.

Usage:
  python monitor.py                  # loop mode (default interval 300 s)
  python monitor.py --once           # single check (used by GitHub Actions)
  python monitor.py --levels         # print the current level sheet and exit
  python monitor.py --test-alert     # send a test notification and exit

Config: config.json if present, otherwise built-in defaults (the desk's
2026-08-17 levels). See config.example.json and README.md.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

BINANCE_BASE = "https://api.binance.com/api/v3/klines"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price"
KRAKEN_OHLC = "https://api.kraken.com/0/public/OHLC"
KRAKEN_TICKER = "https://api.kraken.com/0/public/Ticker"
KUCOIN_CANDLES = "https://api.kucoin.com/api/v1/market/candles"
KUCOIN_STATS = "https://api.kucoin.com/api/v1/market/stats"
USER_AGENT = {"User-Agent": "btc-level-monitor/1.0"}

INTERVAL_MIN = {"1d": 1440, "4h": 240}
KUCOIN_INTERVAL = {"1d": "1day", "4h": "4hour"}

DEFAULT_CONFIG = {
    "provider": "auto",
    "binance_symbol": "BTCUSDT",
    "kraken_pair": "XBTUSD",
    "kucoin_symbol": "BTC-USDT",
    "poll_interval_seconds": 300,
    "notify": {
        "telegram": {
            "enabled": False,
            "token_env": "TELEGRAM_BOT_TOKEN",
            "chat_env": "TELEGRAM_CHAT_ID",
        },
        "discord": {"enabled": False, "webhook_env": "DISCORD_WEBHOOK_URL"},
        "slack": {"enabled": False, "webhook_env": "SLACK_WEBHOOK_URL"},
    },
    "fib": {
        "FIB618": 63439,
        "FIB786": 64986,
        "FIB50": 62352,
        "SHELF65K": 65391,
    },
    "setups": [
        {
            "id": "L1",
            "name": "L1 VWAP/R1 pullback long",
            "side": "long",
            "type": "zone",
            "entry_min": "VWAP",
            "entry_max": "R1",
            "hold_minutes": 30,
            "stop": "PIVOT",
            "t1": "R2",
            "t2": "FIB786",
        },
        {
            "id": "L2",
            "name": "L2 4h-close breakout long",
            "side": "long",
            "type": "close_break",
            "level": "SESSION_HIGH_PRIOR",
            "stop": "R1",
            "t1": "FIB786",
            "t2": "SHELF65K",
            "volume_threshold_btc": 3000,
        },
        {
            "id": "S1",
            "name": "S1 R2 rejection short",
            "side": "short",
            "type": "rejection",
            "zone_min": "R2",
            "zone_max": "R2",
            "zone_tolerance": 30,
            "stop": 64060,
            "t1": "PIVOT",
            "t2": "S1",
            "volume_cap_btc": 1000,
        },
        {
            "id": "S2",
            "name": "S2 4h-close breakdown short",
            "side": "short",
            "type": "close_break",
            "level": "PIVOT",
            "stop": "R1",
            "t1": "S1",
            "t2": "FIB50",
            "volume_threshold_btc": 2000,
        },
    ],
}


def now_utc():
    return datetime.now(timezone.utc)


def utc_ms(dt):
    return int(dt.timestamp() * 1000)


def fmt(n):
    return f"{n:,.0f}"


def http_get_json(url, timeout=15, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=USER_AGENT)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # network / HTTP / JSON errors
            last = e
            time.sleep(2 * (i + 1))
    raise last


def http_post_json(url, payload, timeout=15):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def fetch_binance_klines(symbol, interval, limit):
    url = (
        f"{BINANCE_BASE}?"
        + urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": limit})
    )
    return to_candles(http_get_json(url))


def fetch_binance_price(symbol):
    url = (
        f"{BINANCE_TICKER}?"
        + urllib.parse.urlencode({"symbol": symbol})
    )
    return float(http_get_json(url)["price"])


def fetch_kraken_klines(pair, interval):
    url = (
        f"{KRAKEN_OHLC}?"
        + urllib.parse.urlencode({"pair": pair, "interval": INTERVAL_MIN[interval]})
    )
    data = http_get_json(url)
    if data.get("error"):
        raise RuntimeError(f"kraken error: {data['error']}")
    return to_candles_kraken(data["result"][pair], interval)


def fetch_kraken_price(pair):
    url = (
        f"{KRAKEN_TICKER}?"
        + urllib.parse.urlencode({"pair": pair})
    )
    data = http_get_json(url)
    if data.get("error"):
        raise RuntimeError(f"kraken error: {data['error']}")
    return float(data["result"][pair]["c"][0])


def fetch_kucoin_klines(symbol, interval):
    url = (
        f"{KUCOIN_CANDLES}?"
        + urllib.parse.urlencode({"type": KUCOIN_INTERVAL[interval], "symbol": symbol})
    )
    data = http_get_json(url)
    if data.get("code") != "200000" or not data.get("data"):
        raise RuntimeError(f"kucoin error: {data.get('msg') or data.get('code')}")
    return to_candles_kucoin(data["data"], interval)


def fetch_kucoin_price(symbol):
    url = (
        f"{KUCOIN_STATS}?"
        + urllib.parse.urlencode({"symbol": symbol})
    )
    data = http_get_json(url)
    if data.get("code") != "200000" or not data.get("data"):
        raise RuntimeError(f"kucoin error: {data.get('msg') or data.get('code')}")
    return float(data["data"]["last"])


def fetch_market(cfg):
    provider = cfg.get("provider", "auto")
    order = ["binance", "kraken", "kucoin"] if provider == "auto" else [provider]
    errors = []
    for p in order:
        try:
            if p == "binance":
                kd = fetch_binance_klines(cfg["binance_symbol"], "1d", 60)
                k4 = fetch_binance_klines(cfg["binance_symbol"], "4h", 40)
                price = fetch_binance_price(cfg["binance_symbol"])
            elif p == "kraken":
                kd = fetch_kraken_klines(cfg["kraken_pair"], "1d")
                k4 = fetch_kraken_klines(cfg["kraken_pair"], "4h")
                price = fetch_kraken_price(cfg["kraken_pair"])
            elif p == "kucoin":
                kd = fetch_kucoin_klines(cfg["kucoin_symbol"], "1d")
                k4 = fetch_kucoin_klines(cfg["kucoin_symbol"], "4h")
                price = fetch_kucoin_price(cfg["kucoin_symbol"])
            else:
                raise RuntimeError(f"unknown provider: {p}")
            return p, kd, k4, price
        except Exception as e:
            errors.append(f"{p}: {e}")
    raise RuntimeError("all providers failed: " + "; ".join(errors))


def to_candles(raw):
    out = []
    for r in raw:
        volume = float(r[5])
        out.append(
            {
                "open_time": int(r[0]),
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": volume,
                "close_time": int(r[6]),
                "quote_volume": float(r[7]),
                "vwap": float(r[7]) / volume if volume else float(r[4]),
            }
        )
    return out


def to_candles_kraken(rows, interval):
    span = INTERVAL_MIN[interval] * 60000
    out = []
    for r in rows:
        t = int(r[0]) * 1000
        volume = float(r[6])
        out.append(
            {
                "open_time": t,
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
                "volume": volume,
                "close_time": t + span - 1,
                "quote_volume": float(r[5]) * volume,
                "vwap": float(r[5]),
            }
        )
    return out


def to_candles_kucoin(rows, interval):
    span = INTERVAL_MIN[interval] * 60000
    out = []
    for r in rows:  # [time, open, close, high, low, volume, turnover], newest first
        t = int(r[0]) * 1000
        volume = float(r[5])
        out.append(
            {
                "open_time": t,
                "open": float(r[1]),
                "high": float(r[3]),
                "low": float(r[4]),
                "close": float(r[2]),
                "volume": volume,
                "close_time": t + span - 1,
                "quote_volume": float(r[6]),
                "vwap": float(r[6]) / volume if volume else float(r[2]),
            }
        )
    out.sort(key=lambda k: k["open_time"])
    return out


def closed_candles(candles):
    now = utc_ms(now_utc())
    return [c for c in candles if c["close_time"] <= now]


def compute_levels(klines_1d, klines_4h, fib):
    now = now_utc()
    closed_1d = closed_candles(klines_1d)
    prev = closed_1d[-1]
    h, l, c = prev["high"], prev["low"], prev["close"]
    p = (h + l + c) / 3.0
    levels = {
        "PIVOT": p,
        "R1": 2 * p - l,
        "R2": p + (h - l),
        "R3": h + 2 * (p - l),
        "S1": 2 * p - h,
        "S2": p - (h - l),
        "S3": l - 2 * (h - p),
        "PRIOR_VWAP": prev["vwap"],
        "PRIOR_HIGH": h,
        "PRIOR_LOW": l,
        "PRIOR_CLOSE": c,
    }

    day_start = utc_ms(datetime(now.year, now.month, now.day, tzinfo=timezone.utc))
    session = [k for k in klines_4h if k["open_time"] >= day_start]
    if session:
        base_vol = sum(k["volume"] for k in session)
        levels["VWAP"] = (
            sum(k["vwap"] * k["volume"] for k in session) / base_vol if base_vol else p
        )
        levels["SESSION_HIGH"] = max(k["high"] for k in session)
        levels["SESSION_LOW"] = min(k["low"] for k in session)
        closed_sess = [k for k in session if k["close_time"] <= utc_ms(now)]
        prior_closed = closed_sess[:-1] if len(closed_sess) > 1 else []
        levels["SESSION_HIGH_PRIOR"] = (
            max(k["high"] for k in prior_closed) if prior_closed else h
        )
    else:
        levels["VWAP"] = levels["PRIOR_VWAP"]
        levels["SESSION_HIGH"] = levels["SESSION_HIGH_PRIOR"] = h
        levels["SESSION_LOW"] = l

    levels.update(fib)
    return levels, prev


def atr_14(candles):
    closed = closed_candles(candles)
    if len(closed) < 15:
        return None
    trs = []
    for i in range(-14, 0):
        k = closed[i]
        prev_close = closed[i - 1]["close"]
        trs.append(max(k["high"] - k["low"], abs(k["high"] - prev_close), abs(k["low"] - prev_close)))
    return sum(trs) / 14.0


def avg_volume(candles, n=20):
    closed = closed_candles(candles)
    if not closed:
        return 0.0
    window = closed[-n:]
    return sum(k["volume"] for k in window) / len(window)


def sma(closes, n):
    return sum(closes[-n:]) / n if len(closes) >= n else None


def resolve(ref, levels):
    if isinstance(ref, (int, float)):
        return float(ref)
    if ref in levels:
        return levels[ref]
    raise KeyError(f"unknown level reference: {ref}")


def load_state(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("setups", {})
        data.setdefault("day", None)
        return data
    except (OSError, json.JSONDecodeError):
        return {"setups": {}, "day": None}


def save_state(path, state):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def notify(cfg, text):
    print(text, flush=True)
    n = cfg.get("notify", {})
    tg = n.get("telegram", {})
    if tg.get("enabled"):
        token = os.environ.get(tg.get("token_env", ""), "")
        chat = os.environ.get(tg.get("chat_env", ""), "")
        if token and chat:
            try:
                http_post_json(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    {"chat_id": chat, "text": text},
                )
            except Exception as e:
                print(f"[notify] telegram failed: {e}")
    for name, payload_key in (("discord", "content"), ("slack", "text")):
        d = n.get(name, {})
        if d.get("enabled"):
            hook = os.environ.get(d.get("webhook_env", ""), "")
            if hook:
                try:
                    http_post_json(hook, {payload_key: text})
                except Exception as e:
                    print(f"[notify] {name} failed: {e}")


def eval_zone(cfg, setup, st, price, levels, ctx):
    alerts = []
    lo = resolve(setup["entry_min"], levels)
    hi = resolve(setup["entry_max"], levels)
    stop = resolve(setup["stop"], levels)
    t1 = resolve(setup["t1"], levels)
    t2 = resolve(setup["t2"], levels)
    buffer = ctx["atr4h"] * 0.5 if ctx["atr4h"] else hi * 0.001
    state = st.get("state", "idle")

    if state == "idle":
        if st.get("guard") is not None:
            if price >= lo:
                return alerts
            st["guard"] = None
        if lo <= price <= hi:
            st["state"] = "armed"
            st["armed_at"] = ctx["now_ms"]
            alerts.append(
                f"[ARMED] {setup['id']} {setup['name']}: price {fmt(price)} entered zone "
                f"{fmt(lo)}-{fmt(hi)}. Confirm = holds >= {setup['hold_minutes']} min "
                f"above {fmt(lo)}. Stop {fmt(stop)} | T1 {fmt(t1)} | T2 {fmt(t2)}."
            )
    elif state == "armed":
        held = (ctx["now_ms"] - st["armed_at"]) / 60000.0
        if price < lo - buffer:
            st["state"] = "idle"
            alerts.append(
                f"[INVALIDATED] {setup['id']}: zone lost, price {fmt(price)} < {fmt(lo - buffer)}. No entry."
            )
        elif price > hi + buffer:
            st["state"] = "idle"
            alerts.append(
                f"[MISSED] {setup['id']}: price ran through {fmt(hi)} to {fmt(price)}. Stand aside."
            )
        elif held >= setup["hold_minutes"]:
            st["state"] = "triggered"
            st["triggered_at"] = ctx["now_ms"]
            alerts.append(
                f"[TRIGGERED] {setup['id']} {setup['name']}: held {fmt(lo)}-{fmt(hi)} for "
                f"{int(held)} min. ENTER ~{fmt(max(price, lo))}. Stop {fmt(stop)} | "
                f"T1 {fmt(t1)} | T2 {fmt(t2)}."
            )
    elif state == "triggered":
        if setup["side"] == "long":
            if price <= stop:
                st["state"] = "idle"
                st["guard"] = price
                alerts.append(f"[STOP HIT] {setup['id']}: price {fmt(price)} <= stop {fmt(stop)}. Closed.")
            elif price >= t2:
                st["state"] = "idle"
                st["guard"] = price
                alerts.append(f"[T2 HIT] {setup['id']}: price {fmt(price)} >= {fmt(t2)}. Full exit.")
            elif price >= t1:
                alerts.append(f"[T1 HIT] {setup['id']}: price {fmt(price)} >= {fmt(t1)}. Take partial.")
    return alerts


def eval_close_break(cfg, setup, st, price, levels, ctx, last_closed_4h):
    alerts = []
    level = resolve(setup["level"], levels)
    stop = resolve(setup["stop"], levels)
    t1 = resolve(setup["t1"], levels)
    t2 = resolve(setup["t2"], levels)
    state = st.get("state", "idle")
    candle_time = last_closed_4h["close_time"]
    close = last_closed_4h["close"]
    side = setup["side"]
    broke = (close > level) if side == "long" else (close < level)

    if st.get("last_candle") != candle_time:
        st["last_candle"] = candle_time
        if broke and state == "idle":
            vol = last_closed_4h["volume"]
            need = setup.get("volume_threshold_btc", 0)
            if ctx.get("provider") == "binance":
                verdict = (
                    f"CONFIRMED (vol {fmt(vol)} >= {fmt(need)} BTC)"
                    if vol >= need
                    else f"UNCONFIRMED (vol {fmt(vol)} < {fmt(need)} BTC)"
                )
            else:
                verdict = f"volume not evaluated (src {ctx.get('provider')})"
            st["state"] = "triggered"
            st["triggered_at"] = ctx["now_ms"]
            direction = "ABOVE" if side == "long" else "BELOW"
            alerts.append(
                f"[TRIGGERED] {setup['id']} {setup['name']}: 4h close {fmt(close)} {direction} "
                f"{fmt(level)}. Volume {verdict}. ENTER ~{fmt(close)}. Stop {fmt(stop)} | "
                f"T1 {fmt(t1)} | T2 {fmt(t2)}."
            )
    elif state == "triggered":
        if side == "long":
            if price <= stop:
                st["state"] = "idle"
                alerts.append(f"[STOP HIT] {setup['id']}: price {fmt(price)} <= stop {fmt(stop)}. Closed.")
            elif price >= t2:
                st["state"] = "idle"
                alerts.append(f"[T2 HIT] {setup['id']}: price {fmt(price)} >= {fmt(t2)}. Full exit.")
            elif price >= t1:
                alerts.append(f"[T1 HIT] {setup['id']}: price {fmt(price)} >= {fmt(t1)}. Take partial.")
        else:
            if price >= stop:
                st["state"] = "idle"
                alerts.append(f"[STOP HIT] {setup['id']}: price {fmt(price)} >= stop {fmt(stop)}. Closed.")
            elif price <= t2:
                st["state"] = "idle"
                alerts.append(f"[T2 HIT] {setup['id']}: price {fmt(price)} <= {fmt(t2)}. Full exit.")
            elif price <= t1:
                alerts.append(f"[T1 HIT] {setup['id']}: price {fmt(price)} <= {fmt(t1)}. Take partial.")
    return alerts


def eval_rejection(cfg, setup, st, price, levels, ctx, last_closed_4h):
    alerts = []
    base = resolve(setup["zone_min"], levels)
    tol = setup.get("zone_tolerance", 0)
    lo, hi = base - tol, base + tol
    stop = resolve(setup["stop"], levels)
    t1 = resolve(setup["t1"], levels)
    t2 = resolve(setup["t2"], levels)
    state = st.get("state", "idle")
    candle_time = last_closed_4h["close_time"]
    k = last_closed_4h
    rng = k["high"] - k["low"]
    upper_wick = rng > 0 and (k["high"] - max(k["open"], k["close"])) / rng > 0.4
    bearish = k["close"] < k["open"]
    rejection = bearish or upper_wick

    if state == "idle":
        if lo <= price <= hi and st.get("last_zone_candle") != candle_time:
            st["last_zone_candle"] = candle_time
            cap = setup.get("volume_cap_btc", 0)
            if rejection:
                st["state"] = "triggered"
                st["triggered_at"] = ctx["now_ms"]
                alerts.append(
                    f"[TRIGGERED] {setup['id']} {setup['name']}: price {fmt(price)} in "
                    f"{fmt(lo)}-{fmt(hi)} with rejection on last 4h candle (bearish={bearish}, "
                    f"upper-wick={upper_wick}). ENTER {fmt(lo)}-{fmt(hi)}. Stop {fmt(stop)} | "
                    f"T1 {fmt(t1)} | T2 {fmt(t2)}."
                )
            else:
                alerts.append(
                    f"[R2 TEST] {setup['id']}: price {fmt(price)} touching {fmt(base)} - no "
                    f"rejection yet. Watch for long upper wick / bearish close. Entry only on "
                    f"rejection; cap {fmt(cap)} BTC/4h."
                )
    elif state == "triggered":
        if setup["side"] == "short":
            if price >= stop:
                st["state"] = "idle"
                alerts.append(f"[STOP HIT] {setup['id']}: price {fmt(price)} >= stop {fmt(stop)}. Closed.")
            elif price <= t2:
                st["state"] = "idle"
                alerts.append(f"[T2 HIT] {setup['id']}: price {fmt(price)} <= {fmt(t2)}. Full exit.")
            elif price <= t1:
                alerts.append(f"[T1 HIT] {setup['id']}: price {fmt(price)} <= {fmt(t1)}. Take partial.")
    return alerts


def run_once(cfg, state, print_sheet=True):
    now = now_utc()
    provider, klines_1d, klines_4h, price = fetch_market(cfg)

    levels, prev_day = compute_levels(klines_1d, klines_4h, cfg.get("fib", {}))
    atr = atr_14(klines_4h)
    avg_vol = avg_volume(klines_4h, 20)
    closed_4h = closed_candles(klines_4h)
    last_closed_4h = closed_4h[-1]

    closes_1d = [k["close"] for k in closed_candles(klines_1d)]
    sma9, sma20, sma50 = sma(closes_1d, 9), sma(closes_1d, 20), sma(closes_1d, 50)

    ctx = {
        "now_ms": utc_ms(now),
        "atr4h": atr,
        "avg_vol4h": avg_vol,
        "provider": provider,
    }

    if print_sheet:
        rel = []
        for name, val in (("SMA9", sma9), ("SMA20", sma20), ("SMA50", sma50)):
            if val:
                rel.append(f"px{'<' if price < val else '>'} {name} {fmt(val)}")
        print(
            f"[{now.strftime('%Y-%m-%d %H:%M')} UTC] BTC {fmt(price)} | src {provider} | "
            f"VWAP {fmt(levels['VWAP'])} (prev-day {fmt(levels['PRIOR_VWAP'])}) | "
            f"P {fmt(levels['PIVOT'])} R1 {fmt(levels['R1'])} R2 {fmt(levels['R2'])} R3 {fmt(levels['R3'])} | "
            f"S1 {fmt(levels['S1'])} S2 {fmt(levels['S2'])} S3 {fmt(levels['S3'])} | "
            f"Sess H/L {fmt(levels['SESSION_HIGH'])}/{fmt(levels['SESSION_LOW'])} | "
            f"4h vol {fmt(last_closed_4h['volume'])} (avg {fmt(avg_vol)}) | "
            f"ATR4h {fmt(atr) if atr else 'n/a'} | " + " ".join(rel)
        )

    day_key = prev_day["open_time"] // 86400000
    if state.get("day") != day_key:
        state["day"] = day_key
        notify(
            cfg,
            f"[NEW SESSION] {now.strftime('%Y-%m-%d')} levels: "
            f"P {fmt(levels['PIVOT'])} R1 {fmt(levels['R1'])} R2 {fmt(levels['R2'])} "
            f"S1 {fmt(levels['S1'])} S2 {fmt(levels['S2'])} | "
            f"VWAP {fmt(levels['VWAP'])} | prior day H/L/C {fmt(levels['PRIOR_HIGH'])}/"
            f"{fmt(levels['PRIOR_LOW'])}/{fmt(levels['PRIOR_CLOSE'])}",
        )

    for setup in cfg.get("setups", []):
        st = state["setups"].setdefault(setup["id"], {"state": "idle"})
        kind = setup.get("type")
        if kind == "zone":
            alerts = eval_zone(cfg, setup, st, price, levels, ctx)
        elif kind == "close_break":
            alerts = eval_close_break(cfg, setup, st, price, levels, ctx, last_closed_4h)
        elif kind == "rejection":
            alerts = eval_rejection(cfg, setup, st, price, levels, ctx, last_closed_4h)
        else:
            alerts = []
        for a in alerts:
            notify(cfg, a)


def main():
    ap = argparse.ArgumentParser(description="BTC-USD level monitor")
    ap.add_argument("--once", action="store_true", help="run a single check and exit")
    ap.add_argument("--levels", action="store_true", help="print level sheet once and exit")
    ap.add_argument("--test-alert", action="store_true", help="send a test notification and exit")
    ap.add_argument("--config", default=None, help="path to config.json (default: built-in levels)")
    ap.add_argument("--state", default="state.json", help="path to state file")
    ap.add_argument("--provider", choices=["auto", "binance", "kraken", "kucoin"], default=None, help="force data provider")
    ap.add_argument("--interval", type=int, default=None, help="poll interval seconds (overrides config)")
    args = ap.parse_args()

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))

    if args.provider:
        cfg["provider"] = args.provider

    if args.test_alert:
        notify(cfg, "[TEST] BTC level monitor online - notification channel works.")
        return

    state = load_state(args.state)

    if args.levels:
        try:
            run_once(cfg, state, print_sheet=True)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            sys.exit(1)
        return

    interval = args.interval or cfg.get("poll_interval_seconds", 300)
    while True:
        try:
            run_once(cfg, state, print_sheet=True)
            save_state(args.state, state)
        except Exception as e:
            print(f"[error] {e}", file=sys.stderr)
            if args.once:
                sys.exit(1)
        if args.once:
            break
        time.sleep(interval)


if __name__ == "__main__":
    main()