export const STATIC_LEVELS = {
  FIB618: 63439,
  FIB786: 64986,
  FIB50: 62352,
  SHELF65K: 65391,
};

export const SETUPS = [
  {
    id: "L1",
    name: "L1 VWAP/R1 pullback long",
    side: "long",
    type: "zone",
    entry_min: "VWAP",
    entry_max: "R1",
    hold_minutes: 30,
    stop: "PIVOT",
    t1: "R2",
    t2: "FIB786",
  },
  {
    id: "L2",
    name: "L2 4h-close breakout long",
    side: "long",
    type: "close_break",
    level: "SESSION_HIGH_PRIOR",
    stop: "R1",
    t1: "FIB786",
    t2: "SHELF65K",
    volume_threshold_btc: 3000,
  },
  {
    id: "S1",
    name: "S1 R2 rejection short",
    side: "short",
    type: "rejection",
    zone_min: "R2",
    zone_max: "R2",
    zone_tolerance: 30,
    stop: 64060,
    t1: "PIVOT",
    t2: "S1",
    volume_cap_btc: 1000,
  },
  {
    id: "S2",
    name: "S2 4h-close breakdown short",
    side: "short",
    type: "close_break",
    level: "PIVOT",
    stop: "R1",
    t1: "S1",
    t2: "FIB50",
    volume_threshold_btc: 2000,
  },
];

export function fmt(n) {
  return Math.round(n).toLocaleString("en-US");
}

export function nowUtcMs() {
  return Date.now();
}

async function getJson(url, timeoutMs = 15000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { signal: ctrl.signal, cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status} ${url}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

async function fetchBinance() {
  const base = "https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=";
  const [raw1d, raw4h] = await Promise.all([
    getJson(base + "1d&limit=60"),
    getJson(base + "4h&limit=40"),
  ]);
  const kd = raw1d.map(r => ({
    open_time: r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4],
    volume: +r[5], close_time: r[6], quote_volume: +r[7],
    vwap: +r[7] / +r[5] || +r[4],
  }));
  const k4 = raw4h.map(r => ({
    open_time: r[0], open: +r[1], high: +r[2], low: +r[3], close: +r[4],
    volume: +r[5], close_time: r[6], quote_volume: +r[7],
    vwap: +r[7] / +r[5] || +r[4],
  }));
  const price = k4[k4.length - 1].close;
  return { provider: "binance", kd, k4, price };
}

async function fetchKraken() {
  const base = "https://api.kraken.com/0/public/OHLC?pair=XBTUSD&interval=";
  const [d1, d4] = await Promise.all([getJson(base + "1440"), getJson(base + "240")]);
  if (d1.error && d1.error.length) throw new Error("kraken: " + d1.error.join(","));
  const conv = (rows, span) =>
    rows.map(r => {
      const volume = +r[6];
      return {
        open_time: r[0] * 1000, open: +r[1], high: +r[2], low: +r[3], close: +r[4],
        volume, close_time: r[0] * 1000 + span - 1, quote_volume: +r[5] * volume, vwap: +r[5],
      };
    });
  const kd = conv(d1.result.XBTUSD, 1440 * 60000);
  const k4 = conv(d4.result.XBTUSD, 240 * 60000);
  const price = k4[k4.length - 1].close;
  return { provider: "kraken", kd, k4, price };
}

async function fetchKucoin() {
  const base = "https://api.kucoin.com/api/v1/market/candles?symbol=BTC-USDT&type=";
  const [d1, d4] = await Promise.all([getJson(base + "1day"), getJson(base + "4hour")]);
  const conv = (rows, span) =>
    rows.map(r => {
      const volume = +r[5];
      return {
        open_time: r[0] * 1000, open: +r[1], high: +r[3], low: +r[4], close: +r[2],
        volume, close_time: r[0] * 1000 + span - 1, quote_volume: +r[6],
        vwap: +r[6] / volume || +r[2],
      };
    })
    .sort((a, b) => a.open_time - b.open_time);
  const kd = conv(d1.data, 1440 * 60000);
  const k4 = conv(d4.data, 240 * 60000);
  const price = k4[k4.length - 1].close;
  return { provider: "kucoin", kd, k4, price };
}

export async function fetchMarket(preferred = "auto") {
  const order = preferred === "auto" ? ["binance", "kraken", "kucoin"] : [preferred];
  const errors = [];
  for (const p of order) {
    try {
      if (p === "binance") return await fetchBinance();
      if (p === "kraken") return await fetchKraken();
      if (p === "kucoin") return await fetchKucoin();
    } catch (e) {
      errors.push(`${p}: ${e.message}`);
    }
  }
  throw new Error("all providers failed: " + errors.join("; "));
}

export function closedCandles(candles, nowMs) {
  return candles.filter(c => c.close_time <= nowMs);
}

export function computeLevels(kd, k4, nowMs) {
  const closed1d = closedCandles(kd, nowMs);
  const prev = closed1d[closed1d.length - 1];
  const h = prev.high, l = prev.low, c = prev.close;
  const p = (h + l + c) / 3;
  const levels = {
    PIVOT: p, R1: 2 * p - l, R2: p + (h - l), R3: h + 2 * (p - l),
    S1: 2 * p - h, S2: p - (h - l), S3: l - 2 * (h - p),
    PRIOR_VWAP: prev.vwap, PRIOR_HIGH: h, PRIOR_LOW: l, PRIOR_CLOSE: c,
  };
  const d = new Date(nowMs);
  const dayStart = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate());
  const session = k4.filter(k => k.open_time >= dayStart);
  if (session.length) {
    const baseVol = session.reduce((s, k) => s + k.volume, 0);
    levels.VWAP = baseVol
      ? session.reduce((s, k) => s + k.vwap * k.volume, 0) / baseVol
      : p;
    levels.SESSION_HIGH = Math.max(...session.map(k => k.high));
    levels.SESSION_LOW = Math.min(...session.map(k => k.low));
    const closedSess = session.filter(k => k.close_time <= nowMs);
    const priorClosed = closedSess.length > 1 ? closedSess.slice(0, -1) : [];
    levels.SESSION_HIGH_PRIOR = priorClosed.length
      ? Math.max(...priorClosed.map(k => k.high))
      : h;
  } else {
    levels.VWAP = levels.PRIOR_VWAP;
    levels.SESSION_HIGH = levels.SESSION_HIGH_PRIOR = h;
    levels.SESSION_LOW = l;
  }
  Object.assign(levels, STATIC_LEVELS);
  return { levels, prevDay: prev };
}

export function atr14(k4, nowMs) {
  const closed = closedCandles(k4, nowMs);
  if (closed.length < 15) return null;
  let sum = 0;
  for (let i = closed.length - 14; i < closed.length; i++) {
    const k = closed[i], pc = closed[i - 1].close;
    sum += Math.max(k.high - k.low, Math.abs(k.high - pc), Math.abs(k.low - pc));
  }
  return sum / 14;
}

export function avgVolume(k4, n = 20, nowMs) {
  const closed = closedCandles(k4, nowMs);
  if (!closed.length) return 0;
  const win = closed.slice(-n);
  return win.reduce((s, k) => s + k.volume, 0) / win.length;
}

export function sma(closes, n) {
  return closes.length >= n ? closes.slice(-n).reduce((s, v) => s + v, 0) / n : null;
}

export function resolve(ref, levels) {
  if (typeof ref === "number") return ref;
  if (ref in levels) return levels[ref];
  throw new Error(`unknown level reference: ${ref}`);
}

export function evalZone(setup, st, price, levels, ctx, emit) {
  const lo = resolve(setup.entry_min, levels);
  const hi = resolve(setup.entry_max, levels);
  const stop = resolve(setup.stop, levels);
  const t1 = resolve(setup.t1, levels);
  const t2 = resolve(setup.t2, levels);
  const buffer = ctx.atr4h ? ctx.atr4h * 0.5 : hi * 0.001;
  const state = st.state || "idle";

  if (state === "idle") {
    if (st.guard != null) {
      if (price >= lo) return;
      st.guard = null;
    }
    if (lo <= price && price <= hi) {
      st.state = "armed";
      st.armed_at = ctx.nowMs;
      emit(`[ARMED] ${setup.id} ${setup.name}: price ${fmt(price)} entered zone ${fmt(lo)}-${fmt(hi)}. Confirm = holds >= ${setup.hold_minutes} min above ${fmt(lo)}. Stop ${fmt(stop)} | T1 ${fmt(t1)} | T2 ${fmt(t2)}.`);
    }
  } else if (state === "armed") {
    const held = (ctx.nowMs - st.armed_at) / 60000;
    if (price < lo - buffer) {
      st.state = "idle";
      emit(`[INVALIDATED] ${setup.id}: zone lost, price ${fmt(price)} < ${fmt(lo - buffer)}. No entry.`);
    } else if (price > hi + buffer) {
      st.state = "idle";
      emit(`[MISSED] ${setup.id}: price ran through ${fmt(hi)} to ${fmt(price)}. Stand aside.`);
    } else if (held >= setup.hold_minutes) {
      st.state = "triggered";
      st.triggered_at = ctx.nowMs;
      emit(`[TRIGGERED] ${setup.id} ${setup.name}: held ${fmt(lo)}-${fmt(hi)} for ${Math.floor(held)} min. ENTER ~${fmt(Math.max(price, lo))}. Stop ${fmt(stop)} | T1 ${fmt(t1)} | T2 ${fmt(t2)}.`);
    }
  } else if (state === "triggered") {
    if (setup.side === "long") {
      if (price <= stop) {
        st.state = "idle";
        st.guard = price;
        emit(`[STOP HIT] ${setup.id}: price ${fmt(price)} <= stop ${fmt(stop)}. Closed.`);
      } else if (price >= t2) {
        st.state = "idle";
        st.guard = price;
        emit(`[T2 HIT] ${setup.id}: price ${fmt(price)} >= ${fmt(t2)}. Full exit.`);
      } else if (price >= t1) {
        emit(`[T1 HIT] ${setup.id}: price ${fmt(price)} >= ${fmt(t1)}. Take partial.`);
      }
    }
  }
}

export function evalCloseBreak(setup, st, price, levels, ctx, last, emit) {
  const level = resolve(setup.level, levels);
  const stop = resolve(setup.stop, levels);
  const t1 = resolve(setup.t1, levels);
  const t2 = resolve(setup.t2, levels);
  const state = st.state || "idle";
  const candleTime = last.close_time;
  const close = last.close;
  const side = setup.side;
  const broke = side === "long" ? close > level : close < level;

  if (st.last_candle !== candleTime) {
    st.last_candle = candleTime;
    if (broke && state === "idle") {
      const vol = last.volume;
      const need = setup.volume_threshold_btc || 0;
      const verdict = ctx.provider === "binance"
        ? (vol >= need ? `CONFIRMED (vol ${fmt(vol)} >= ${fmt(need)} BTC)` : `UNCONFIRMED (vol ${fmt(vol)} < ${fmt(need)} BTC)`)
        : `volume not evaluated (src ${ctx.provider})`;
      st.state = "triggered";
      st.triggered_at = ctx.nowMs;
      emit(`[TRIGGERED] ${setup.id} ${setup.name}: 4h close ${fmt(close)} ${side === "long" ? "ABOVE" : "BELOW"} ${fmt(level)}. Volume ${verdict}. ENTER ~${fmt(close)}. Stop ${fmt(stop)} | T1 ${fmt(t1)} | T2 ${fmt(t2)}.`);
    }
  } else if (state === "triggered") {
    if (side === "long") {
      if (price <= stop) { st.state = "idle"; emit(`[STOP HIT] ${setup.id}: price ${fmt(price)} <= stop ${fmt(stop)}. Closed.`); }
      else if (price >= t2) { st.state = "idle"; emit(`[T2 HIT] ${setup.id}: price ${fmt(price)} >= ${fmt(t2)}. Full exit.`); }
      else if (price >= t1) { emit(`[T1 HIT] ${setup.id}: price ${fmt(price)} >= ${fmt(t1)}. Take partial.`); }
    } else {
      if (price >= stop) { st.state = "idle"; emit(`[STOP HIT] ${setup.id}: price ${fmt(price)} >= stop ${fmt(stop)}. Closed.`); }
      else if (price <= t2) { st.state = "idle"; emit(`[T2 HIT] ${setup.id}: price ${fmt(price)} <= ${fmt(t2)}. Full exit.`); }
      else if (price <= t1) { emit(`[T1 HIT] ${setup.id}: price ${fmt(price)} <= ${fmt(t1)}. Take partial.`); }
    }
  }
}

export function evalRejection(setup, st, price, levels, ctx, last, emit) {
  const base = resolve(setup.zone_min, levels);
  const tol = setup.zone_tolerance || 0;
  const lo = base - tol, hi = base + tol;
  const stop = resolve(setup.stop, levels);
  const t1 = resolve(setup.t1, levels);
  const t2 = resolve(setup.t2, levels);
  const state = st.state || "idle";
  const candleTime = last.close_time;
  const k = last;
  const rng = k.high - k.low;
  const upperWick = rng > 0 && (k.high - Math.max(k.open, k.close)) / rng > 0.4;
  const bearish = k.close < k.open;
  const rejection = bearish || upperWick;

  if (state === "idle") {
    if (lo <= price && price <= hi && st.last_zone_candle !== candleTime) {
      st.last_zone_candle = candleTime;
      const cap = setup.volume_cap_btc || 0;
      if (rejection) {
        st.state = "triggered";
        st.triggered_at = ctx.nowMs;
        emit(`[TRIGGERED] ${setup.id} ${setup.name}: price ${fmt(price)} in ${fmt(lo)}-${fmt(hi)} with rejection on last 4h candle (bearish=${bearish}, upper-wick=${upperWick}). ENTER ${fmt(lo)}-${fmt(hi)}. Stop ${fmt(stop)} | T1 ${fmt(t1)} | T2 ${fmt(t2)}.`);
      } else {
        emit(`[R2 TEST] ${setup.id}: price ${fmt(price)} touching ${fmt(base)} - no rejection yet. Watch for long upper wick / bearish close. Entry only on rejection; cap ${fmt(cap)} BTC/4h.`);
      }
    }
  } else if (state === "triggered") {
    if (setup.side === "short") {
      if (price >= stop) { st.state = "idle"; emit(`[STOP HIT] ${setup.id}: price ${fmt(price)} >= stop ${fmt(stop)}. Closed.`); }
      else if (price <= t2) { st.state = "idle"; emit(`[T2 HIT] ${setup.id}: price ${fmt(price)} <= ${fmt(t2)}. Full exit.`); }
      else if (price <= t1) { emit(`[T1 HIT] ${setup.id}: price ${fmt(price)} <= ${fmt(t1)}. Take partial.`); }
    }
  }
}

export function evaluateAll(cfg, state, market, emit) {
  const nowMs = nowUtcMs();
  const { levels, prevDay } = computeLevels(market.kd, market.k4, nowMs);
  const atr = atr14(market.k4, nowMs);
  const avgVol = avgVolume(market.k4, 20, nowMs);
  const closed4h = closedCandles(market.k4, nowMs);
  const lastClosed4h = closed4h[closed4h.length - 1];
  const closes1d = closedCandles(market.kd, nowMs).map(k => k.close);
  const ctx = { nowMs, atr4h: atr, avgVol4h: avgVol, provider: market.provider };

  const dayKey = Math.floor(prevDay.open_time / 86400000);
  if (state.day !== dayKey) {
    state.day = dayKey;
    for (const s of Object.keys(state.setups)) state.setups[s] = { state: "idle" };
  }

  for (const setup of cfg.setups) {
    const st = (state.setups[setup.id] = state.setups[setup.id] || { state: "idle" });
    if (setup.type === "zone") evalZone(setup, st, market.price, levels, ctx, emit);
    else if (setup.type === "close_break") evalCloseBreak(setup, st, market.price, levels, ctx, lastClosed4h, emit);
    else if (setup.type === "rejection") evalRejection(setup, st, market.price, levels, ctx, lastClosed4h, emit);
  }

  return {
    price: market.price,
    provider: market.provider,
    levels,
    prevDay,
    atr,
    avgVol,
    lastClosed4h,
    sma9: sma(closes1d, 9),
    sma20: sma(closes1d, 20),
    sma50: sma(closes1d, 50),
    setups: state.setups,
    dayKey,
  };
}