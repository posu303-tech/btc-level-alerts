# BTC-USD Level Monitor

A dependency-free Python monitor that watches Bitcoin and alerts when the
sell-side desk's trade setups trigger. It polls the **Binance public market
data API (no API key required)**, recomputes session levels every run, and
evaluates the four setups from the desk brief dated **2026-08-17** against
the live price and 4h candle closes.

Runs anywhere: your machine (`python monitor.py`), a scheduled task, or
24/7 for free on GitHub Actions (every 15 min) with alerts pushed to
Telegram / Discord / Slack.

## The setups being monitored

| ID | Setup (from desk brief) | Trigger | Stop | T1 | T2 |
|----|-------------------------|---------|------|----|----|
| L1 | Long - VWAP/R1 pullback | Price enters VWAP..R1 zone **and holds >= 30 min** | Below Pivot | R2 | 78.6% fib |
| L2 | Long - 4h-close breakout | 4h candle **closes above** prior session high | R1 | 78.6% fib | 65,391 shelf |
| S1 | Short - R2 rejection | Price in R2 zone **+ rejection** (bearish 4h close or long upper wick) | 64,060 | Pivot | S1 |
| S2 | Short - 4h-close breakdown | 4h candle **closes below** Pivot | R1 | S1 | 50% fib |

All entry/stop/target references resolve against automatically computed
session levels, so pivots, VWAP, session high/low and prior-day VWAP rotate
daily with no config changes.

## Quick start (local)

```bash
# Python 3.8+ only - no pip installs
python monitor.py                 # loop mode, checks every 300 s
python monitor.py --levels        # print the level sheet once and exit
python monitor.py --once          # single check (what GitHub Actions runs)
python monitor.py --test-alert    # verify your notification channel
python monitor.py --interval 60   # faster polling for a local session
```

Each poll prints a level sheet, e.g.:

```
[2026-08-17 12:00 UTC] BTC 63,530 | VWAP 63,310 (prev-day 63,070) | P 63,002 R1 63,288 R2 63,676 R3 63,962 | S1 62,614 S2 62,328 S3 61,940 | Sess H/L 63,580/62,751 | 4h vol 1,201 (avg 1,845) | ATR4h 325 | px> SMA9 63,556 px< SMA20 63,827 px< SMA50 63,614
```

Alert messages look like this:

```
[ARMED] L1 L1 VWAP/R1 pullback long: price 63,290 entered zone 63,275-63,288. Confirm = holds >= 30 min above 63,275. Stop 63,002 | T1 63,676 | T2 64,986.
[TRIGGERED] L2 L2 4h-close breakout long: 4h close 63,600 ABOVE 63,580. Volume CONFIRMED (vol 3,214 >= 3,000 BTC). ENTER ~63,600. Stop 63,288 | T1 64,986 | T2 65,391.
[R2 TEST] S1 S1 R2 rejection short: price 63,680 touching 63,676 - no rejection yet. Watch for long upper wick / bearish close. Entry only on rejection; cap 1,000 BTC/4h.
[STOP HIT] S2 S2 4h-close breakdown short: price 63,290 >= stop 63,288. Closed.
```

## 24/7 monitoring via GitHub Actions (free)

1. Push this repo to GitHub.
2. Add a notification secret under **Settings -> Secrets and variables -> Actions**:
   - Telegram: `TELEGRAM_BOT_TOKEN` (from @BotFather) and `TELEGRAM_CHAT_ID`
   - or Discord: `DISCORD_WEBHOOK_URL`
   - or Slack: `SLACK_WEBHOOK_URL`
3. Enable the workflow under **Actions** tab (it runs every 15 min by default;
   GitHub allows cron as frequently as every 5 min - edit
   `.github/workflows/monitor.yml`).
4. Set `"enabled": true` for your channel in `config.json` (see below).

The workflow evaluates setups on each run, deduplicates alerts via the
committed `state.json`, and pushes updated state back to the repo (a new
commit appears roughly every 15 min - that is expected).

**Note:** scheduled workflows can be delayed by minutes under GitHub load,
so 4h-close triggers are detected within ~0-15 min of the candle close. If
you need tighter timing for session-open decisions, also run the script
locally during the session.

## Live browser dashboard (every 5 s)

The `dashboard/` folder is a self-contained, dependency-free web app
deployed to GitHub Pages (live at
`https://posu303-tech.github.io/btc-level-alerts/`):

- Polls the same public market data (Binance -> Kraken -> KuCoin fallback)
  **every 5 seconds** straight from your browser - no server, no API keys.
- Recomputes all session levels (pivots, session VWAP, session H/L,
  prior-session high, ATR, SMAs) and re-evaluates **L1/L2/S1/S2** on every
  tick, with the same state machines as `monitor.py` (idle -> armed ->
  triggered, stop/T1/T2 hits).
- Shows live trigger status per setup, the full level sheet, a price banner
  and a rolling event feed of every alert the rules fire.
- State is kept in `localStorage`, so a page refresh does not re-trigger or
  double-count setups; it resets only when the pivot day rolls at 00:00 UTC.

To view it locally:

```bash
python -m http.server 8000 --directory dashboard
# open http://localhost:8000
```

Deployment is automatic: any push touching `dashboard/**` or
`.github/workflows/pages.yml` re-deploys via GitHub Actions (workflow
`pages.yml`). The dashboard only evaluates while the tab is open - for
24/7 monitoring that does not depend on a browser, keep the Actions
workflow (`monitor.yml`) and/or the local monitor running.

## Configuration

Copy `config.example.json` to `config.json` and edit. Nothing is required to
run - the built-in defaults equal the example.

Level references (strings) are resolved each run:

| Reference | Meaning |
|---|---|
| `PIVOT`, `R1`-`R3`, `S1`-`S3` | Floor pivots of the last closed daily candle |
| `VWAP` | Session VWAP anchored at 00:00 UTC (includes in-progress 4h candle) |
| `PRIOR_VWAP` | Previous full day's VWAP (daily volume-weighted) |
| `SESSION_HIGH` / `SESSION_LOW` | Current session high / low |
| `SESSION_HIGH_PRIOR` | Session high **before the last closed 4h candle** (breakout reference, does not chase price) |
| `PRIOR_HIGH/LOW/CLOSE` | Last closed daily candle OHLC |
| `FIB618`, `FIB786`, `FIB50`, `SHELF65K` | Static levels from the desk brief (see below) |

### Setups - per-type options

- `zone` (L1): `entry_min`, `entry_max`, `hold_minutes`, `stop`, `t1`, `t2`.
  State machine: idle -> armed (zone touched) -> triggered (held N min) ->
  stop/T1/T2 hits. Re-arms only after price leaves the zone.
- `close_break` (L2, S2): `level`, `side`, `volume_threshold_btc`, `stop`,
  `t1`, `t2`. Fires at most once per 4h candle, on the **closed** candle.
  Volume verdict (`CONFIRMED`/`UNCONFIRMED`) is included in the alert.
- `rejection` (S1): `zone_min`, `zone_max`, `zone_tolerance`, `stop`, `t1`,
  `t2`, `volume_cap_btc`. Alerts "R2 TEST" on zone touch, `TRIGGERED` when
  the last closed 4h candle is bearish or has an upper wick > 40% of range.

### Updating levels for a new desk brief

- Auto levels (pivots, VWAP, session H/L) update every day at 00:00 UTC on
  their own.
- Static levels - `fib.fib618/fib786/fib50` and the 65,391 shelf - are dated
  to the **2026-08-17** brief. Replace them when the desk publishes new
  swing-based levels.
- Prior day's high/low/close used for pivots are taken from Binance; the
  desk brief's own pivot numbers will match these inputs to within exchange
  skew (< $100 typically).

## How levels are computed

- **Floor pivots**: classic formula on prior closed daily candle H/L/C.
- **Session VWAP**: sum(quote volume) / sum(base volume) over all 4h candles
  opened since 00:00 UTC (includes the in-progress candle; the desk's brief
  VWAP used the same convention).
- **ATR(14)**: mean true range of the last 14 closed 4h candles.
- **Volume context**: 4h candle volume vs the mean of the last 20 closed 4h
  candles; daily context via the 20-candle 1d window.
- **SMA9/20/50**: simple means of closed daily closes.

## Data, limitations, honesty

- **Provider**: defaults to `"provider": "auto"` - tries Binance spot
  (`binance_symbol`, default `BTCUSDT`), then Kraken (`kraken_pair`,
  default `XBTUSD`), then KuCoin (`kucoin_symbol`, default `BTC-USDT`).
  Binance is geo-blocked on GitHub Actions US runners (HTTP 451) and
  Kraken is geo-blocked in some regions - the fallback chain exists so at
  least one source works from anywhere. Force one with
  `--provider binance|kraken|kucoin|auto` or in config. Prices are
  typically < 1 s old, but no guarantee of tick accuracy.
- Volume thresholds (`volume_threshold_btc`, `volume_cap_btc`) are tuned to
  Binance volumes; on Kraken the verdict reads "volume not evaluated".
- No options/GEX/max-pain data in this tool - the desk brief flags
  Deribit expiry Aug 28 as the next derivative catalyst; verify with your
  options vendor.
- Weekend candles are low-volume and pivots built on them are softer -
  treat Sunday-derived levels with more caution.
- The monitor alerts when **levels and rules** from the desk brief trigger.
  It does not recommend trades, size positions, or handle execution.
  This is planning software, not investment advice.