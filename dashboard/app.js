import { SETUPS, STATIC_LEVELS, evaluateAll, fetchMarket, fmt, resolve, nowUtcMs } from "./core.js";

const POLL_MS = 5000;
const STATE_KEY = "btc-dash-state-v1";

const $ = id => document.getElementById(id);
const state = loadState();
const events = state.events || (state.events = []);
const alertsSeen = new Set(state.alertsSeen || []);
state.setups = state.setups || {};
let tickInFlight = false;
let lastTick = null;
let firstError = null;

function loadState() {
  try {
    const raw = localStorage.getItem(STATE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* fresh state */ }
  return { setups: {}, day: null, events: [], alertsSeen: [] };
}

function saveState() {
  state.events = events.slice(0, 40);
  state.alertsSeen = [...alertsSeen].slice(-200);
  try {
    localStorage.setItem(STATE_KEY, JSON.stringify(state));
  } catch (e) { /* storage full/blocked - ignore */ }
}

function emit(msg) {
  const entry = { t: nowUtcMs(), msg };
  if (!alertsSeen.has(msg)) {
    alertsSeen.add(msg);
    events.unshift(entry);
  } else {
    const dup = events.find(e => e.msg === msg);
    if (dup) dup.last = nowUtcMs();
  }
  saveState();
  renderEvents();
}

function badgeHtml(stateName) {
  const cls = {
    idle: "idle", armed: "armed", triggered: "triggered",
  }[stateName] || "ended";
  const label = stateName.toUpperCase();
  return `<span class="badge ${cls}">${label}</span>`;
}

function statRow(label, value, extra = "") {
  return `<div class="stat"><span>${label}</span><b>${value}</b>${extra}</div>`;
}

function setupDetails(setup, st, levels, price, report) {
  const html = [];
  try {
    const stop = resolve(setup.stop, levels);
    const t1 = resolve(setup.t1, levels);
    const t2 = resolve(setup.t2, levels);
    const ref = v => (typeof v === "number" ? "" : ` <i>(${v})</i>`);
    html.push(statRow("Stop", fmt(stop), ref(setup.stop)));
    html.push(statRow("T1", fmt(t1), ref(setup.t1)));
    html.push(statRow("T2", fmt(t2), ref(setup.t2)));
  } catch (e) { /* levels not ready */ }
  if (setup.type === "zone") {
    const lo = resolve(setup.entry_min, levels);
    const hi = resolve(setup.entry_max, levels);
    html.push(statRow("Zone", `${fmt(lo)}-${fmt(hi)}`, ` <i>(${setup.entry_min}..${setup.entry_max})</i>`));
    html.push(statRow("Hold rule", `${setup.hold_minutes} min`));
    if (st.state === "armed") {
      const held = ((nowUtcMs() - st.armed_at) / 60000).toFixed(1);
      html.push(statRow("Held", `${held} / ${setup.hold_minutes} min`));
    }
  } else if (setup.type === "close_break") {
    html.push(statRow("Break level", fmt(levels[setup.level]), ` <i>(${setup.level})</i>`));
    html.push(statRow("Vol need", `${fmt(setup.volume_threshold_btc)} BTC/4h`));
    html.push(statRow("Last 4h vol", fmt(report.lastClosed4h.volume)));
  } else if (setup.type === "rejection") {
    const base = resolve(setup.zone_min, levels);
    const tol = setup.zone_tolerance || 0;
    html.push(statRow("Zone", `${fmt(base - tol)}-${fmt(base + tol)}`, ` <i>(${setup.zone_min} ± ${tol})</i>`));
    html.push(statRow("Vol cap", `${fmt(setup.volume_cap_btc)} BTC/4h`));
    const k = report.lastClosed4h;
    const rng = k.high - k.low;
    const wick = rng > 0 ? Math.round(((k.high - Math.max(k.open, k.close)) / rng) * 100) : 0;
    html.push(statRow("Last 4h", `${fmt(k.close)} ${k.close < k.open ? "red" : "green"} · wick ${wick}%`, " <i>(> 40% = reject)</i>"));
  }
  const pnl = price - (setup.type === "rejection"
    ? resolve(setup.zone_min, levels)
    : setup.type === "zone" ? resolve(setup.entry_min, levels) : levels.VWAP || price);
  void pnl;
  return html.join("");
}

function renderHeader(report, meta) {
  const { price, prevDay, provider } = report;
  const delta = price - prevDay.close;
  const pct = (delta / prevDay.close) * 100;
  const dCls = delta >= 0 ? "up" : "down";
  $("price").textContent = fmt(price);
  $("delta").textContent = `${delta >= 0 ? "+" : ""}${fmt(delta)} (${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%) vs prior close ${fmt(prevDay.close)}`;
  $("delta").className = dCls;
  $("meta").textContent = `${provider.toUpperCase()} · ${meta}`;
  $("vls").textContent = `VWAP ${fmt(report.levels.VWAP)} · prev-day VWAP ${fmt(report.levels.PRIOR_VWAP)} · session H/L ${fmt(report.levels.SESSION_HIGH)}/${fmt(report.levels.SESSION_LOW)}`;
}

function renderLevels(report) {
  const { levels, atr, sma9, sma20, sma50, price } = report;
  const row = (label, value, note = "") => {
    const cls = Math.abs(price - value) < (atr || 200) * 0.15 ? "near" : "";
    return `<tr><td>${label}</td><td class="${cls}">${fmt(value)}</td><td>${note}</td></tr>`;
  };
  const smaNote = (v) => v == null ? "n/a" : `${price < v ? "px <" : "px >"} ${fmt(v)}`;
  const lv = levels;
  $("levelsBody").innerHTML = [
    row("Pivot", lv.PIVOT, "floor formula"),
    row("R1", lv.R1, "L1 T2 · L2 T1"),
    row("R2", lv.R2, "S1 stop"),
    row("R3", lv.R3),
    row("S1", lv.S1, "L1 stop · S2 break level"),
    row("S2", lv.S2, "S2 T2"),
    row("S3", lv.S3),
    row("Session VWAP", lv.VWAP, "anchored 00:00 UTC · L2 stop"),
    row("Prior-day VWAP", lv.PRIOR_VWAP),
    row("Prior-day high", lv.PRIOR_HIGH, "L2 breakout ref"),
    row("61.8% fib", STATIC_LEVELS.FIB618, "S1 zone max"),
    row("78.6% fib", STATIC_LEVELS.FIB786, "S2 T1"),
    row("50% fib", STATIC_LEVELS.FIB50, "static brief"),
    row("VAH 65,500", STATIC_LEVELS.VAH, "L2 T2 · S1 zone min"),
    row("ATR (4h)", atr || 0, atr == null ? "warming up" : "14 closed candles"),
    row("SMA9 / 20 / 50", (sma9 || 0), smaNote(sma9)),
  ].join("");
  $("levelsSub").textContent =
    `SMA20 ${sma20 == null ? "n/a" : fmt(sma20)} (${sma20 != null && price < sma20 ? "px below" : "px above"}) · SMA50 ${sma50 == null ? "n/a" : fmt(sma50)} (${sma50 != null && price < sma50 ? "px below" : "px above"}) · avg 4h vol ${fmt(report.avgVol)} BTC · last 4h ${fmt(report.lastClosed4h.close)} (vol ${fmt(report.lastClosed4h.volume)})`;
}

function renderSetups(report) {
  const price = report.price;
  const levels = report.levels;
  for (const setup of SETUPS) {
    const st = report.setups[setup.id] || { state: "idle" };
    const el = $(`card-${setup.id}`);
    const name = setup.name;
    let cond = "";
    if (setup.type === "zone") {
      const lo = resolve(setup.entry_min, levels);
      const hi = resolve(setup.entry_max, levels);
      cond = `Trigger: price in ${fmt(lo)}-${fmt(hi)} (${setup.entry_min}..${setup.entry_max}) holding ≥ ${setup.hold_minutes} min. Entry ${fmt(lo)}-${fmt(hi)}.`;
    } else if (setup.type === "close_break") {
      cond = `Trigger: 4h close ${setup.side === "long" ? "above" : "below"} ${fmt(levels[setup.level])} (${setup.level}) with vol ${setup.side === "long" ? "≥" : "≥"} ${fmt(setup.volume_threshold_btc)} BTC. Entry ~4h close.`;
    } else {
      const base = resolve(setup.zone_min, levels);
      const tol = setup.zone_tolerance || 0;
      cond = `Trigger: price at ${fmt(base)}±${tol} + rejection (bearish 4h close or upper wick > 40%). Entry ${fmt(base - tol)}-${fmt(base + tol)}. Cap ${fmt(setup.volume_cap_btc)} BTC/4h.`;
    }
    const pnl = st.state === "triggered" ? (
      setup.side === "long"
        ? `PnL ${fmt(price - (setup.type === "zone" ? resolve(setup.entry_min, levels) : report.lastClosed4h.close))}`
        : `PnL ${fmt((setup.type === "rejection" ? resolve(setup.zone_min, levels) : report.lastClosed4h.close) - price)}`
    ) : "";
    el.innerHTML = `
      <div class="card-head">
        <div>
          <div class="card-title">${setup.id} <span class="side ${setup.side}">${setup.side.toUpperCase()}</span></div>
          <div class="card-sub">${name}</div>
        </div>
        <div class="head-right">${badgeHtml(st.state)}${pnl ? `<span class="pnl">${pnl}</span>` : ""}</div>
      </div>
      <div class="cond">${cond}</div>
      <div class="stats">${setupDetails(setup, st, levels, price, report)}</div>
      ${st.triggered_at ? `<div class="note">Triggered ${new Date(st.triggered_at).toISOString().slice(0, 19).replace("T", " ")} UTC</div>` : ""}
    `;
  }
}

function renderEvents() {
  const el = $("events");
  if (!events.length) {
    el.innerHTML = `<div class="empty">No events yet - watching for setups.</div>`;
    return;
  }
  el.innerHTML = events.slice(0, 40).map(e => {
    const t = new Date(e.t).toISOString().slice(11, 19);
    const tag = e.msg.slice(0, e.msg.indexOf("]") + 1).replace("[", "").replace("]", "");
    return `<div class="event"><span class="evt-time">${t}</span><span class="evt-tag">${tag}</span><span>${e.msg}</span></div>`;
  }).join("");
}

function renderMeta() {
  $("tick").textContent = lastTick
    ? `Last tick ${lastTick.toISOString().slice(11, 19)} UTC · polls every ${POLL_MS / 1000} s`
    : "Connecting…";
  if (firstError) $("err").textContent = `Data source error: ${firstError}`;
  else $("err").textContent = "";
}

async function tick() {
  if (tickInFlight) return;
  tickInFlight = true;
  try {
    const market = await fetchMarket();
    firstError = null;
    const report = evaluateAll({ setups: SETUPS }, state, market, emit);
    renderHeader(report, `${new Date(report.levels ? report.prevDay.open_time : 0).toISOString().slice(0, 10)} pivot day`);
    renderLevels(report);
    renderSetups(report);
    lastTick = new Date();
    saveState();
  } catch (e) {
    firstError = e.message;
    renderMeta();
  } finally {
    tickInFlight = false;
    renderMeta();
  }
}

$("refresh").addEventListener("click", () => {
  lastTick = null;
  tick();
});

renderEvents();
renderMeta();
tick();
setInterval(tick, POLL_MS);