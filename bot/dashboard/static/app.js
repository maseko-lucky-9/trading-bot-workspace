/*
 * MT5 Bot Dashboard — polling client.
 *
 * Hits /api/health, /api/equity, /api/trades, /api/metrics every 7s
 * via Promise.allSettled so one failing endpoint never blanks the page.
 */
"use strict";

const POLL_MS = 7000;

let equityChart = null;
let lastTradesPayload = null;
let sortKey = "close_time";
let sortDir = "desc";

const $ = (id) => document.getElementById(id);

function fmtNum(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (typeof x !== "number") x = Number(x);
  if (!Number.isFinite(x)) return "—";
  return x.toFixed(digits);
}
function fmtPct(x, digits = 2) {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (typeof x !== "number") x = Number(x);
  if (!Number.isFinite(x)) return "—";
  return (x * 100).toFixed(digits) + "%";
}

function setKlass(el, klass) {
  el.classList.remove("ok", "warn", "bad");
  if (klass) el.classList.add(klass);
}

async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// --------------------------------------------------------------------- //
// Pane: Health                                                          //
// --------------------------------------------------------------------- //

function renderHealth(payload) {
  if (!payload) return;
  const proc = payload.process || {};
  const bridge = payload.bridge || {};
  const regime = payload.regime || {};
  const cb = payload.circuit_breaker || {};

  const procEl = $("health-process");
  procEl.textContent = proc.status || "—";
  setKlass(procEl, proc.status === "running" ? "ok" : (proc.status === "not_running" ? "bad" : "warn"));

  $("health-pid").textContent = proc.pid ? `${proc.pid} / ${proc.etime || "?"}` : "—";

  const bEl = $("health-bridge");
  bEl.textContent = bridge.status || "—";
  setKlass(bEl, bridge.status === "ok" ? "ok" : "bad");

  const eaEl = $("health-ea");
  eaEl.textContent = bridge.ea_connected === true ? "yes" : (bridge.ea_connected === false ? "no" : "—");
  setKlass(eaEl, bridge.ea_connected ? "ok" : (bridge.ea_connected === false ? "warn" : null));

  $("health-latency").textContent = bridge.latency_ms != null ? `${bridge.latency_ms} ms` : "—";

  const rEl = $("health-regime");
  rEl.textContent = regime.label || "—";
  setKlass(rEl, regime.status === "ok" ? "ok" : "warn");

  const ddEl = $("health-dd");
  if (cb.peak_equity != null) {
    ddEl.textContent = `${fmtPct(cb.current_drawdown)} (peak $${fmtNum(cb.peak_equity)}, ${cb.trade_count} trades)`;
    const dd = cb.current_drawdown || 0;
    setKlass(ddEl, dd >= 0.20 ? "bad" : (dd >= 0.10 ? "warn" : "ok"));
  } else {
    ddEl.textContent = "—";
    setKlass(ddEl, null);
  }
}

// --------------------------------------------------------------------- //
// Pane: Equity                                                          //
// --------------------------------------------------------------------- //

function renderEquity(payload) {
  if (!payload || payload.status !== "ok") return;
  const labels = payload.timestamps || [];
  const equity = payload.equity || [];
  const peak = payload.peak || [];
  const dd = (payload.drawdown || []).map((d) => -d * 100);

  const ctx = $("equity-chart").getContext("2d");
  if (!equityChart) {
    equityChart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Equity",
            data: equity,
            borderColor: "#60a5fa",
            backgroundColor: "rgba(96,165,250,0.1)",
            tension: 0.1,
            yAxisID: "y",
            pointRadius: 0,
          },
          {
            label: "Peak",
            data: peak,
            borderColor: "#9aa0a6",
            borderDash: [4, 4],
            tension: 0,
            yAxisID: "y",
            pointRadius: 0,
          },
          {
            label: "Drawdown %",
            data: dd,
            borderColor: "#f87171",
            backgroundColor: "rgba(248,113,113,0.15)",
            fill: true,
            tension: 0.1,
            yAxisID: "y1",
            pointRadius: 0,
          },
        ],
      },
      options: {
        responsive: true,
        animation: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#9aa0a6", font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: "#9aa0a6", maxTicksLimit: 8, font: { size: 10 } }, grid: { color: "#2a2a2e" } },
          y: { ticks: { color: "#9aa0a6", font: { size: 10 } }, grid: { color: "#2a2a2e" }, position: "left" },
          y1: { ticks: { color: "#9aa0a6", font: { size: 10 } }, grid: { display: false }, position: "right", suggestedMin: -50, suggestedMax: 0 },
        },
      },
    });
  } else {
    equityChart.data.labels = labels;
    equityChart.data.datasets[0].data = equity;
    equityChart.data.datasets[1].data = peak;
    equityChart.data.datasets[2].data = dd;
    equityChart.update("none");
  }

  const last = equity.length ? equity[equity.length - 1] : null;
  $("equity-meta").textContent =
    last != null
      ? `equity $${fmtNum(last)} · peak $${fmtNum(payload.peak_equity)} · dd ${fmtPct(payload.current_drawdown)} · ${equity.length} trades`
      : "no closed trades yet";
}

// --------------------------------------------------------------------- //
// Pane: Metrics                                                         //
// --------------------------------------------------------------------- //

function renderMetrics(m) {
  if (!m) return;
  if (m.status !== "ok") {
    ["m-sharpe", "m-dsr", "m-expectancy", "m-winrate", "m-payoff", "m-trades"].forEach((id) => ($(id).textContent = "—"));
    return;
  }
  $("m-sharpe").textContent = fmtNum(m.sharpe);
  $("m-dsr").textContent = fmtPct(m.dsr);
  $("m-expectancy").textContent = `$${fmtNum(m.expectancy)}`;
  $("m-winrate").textContent = fmtPct(m.win_rate);
  $("m-payoff").textContent = fmtNum(m.payoff_ratio);
  $("m-trades").textContent = m.trade_count != null ? String(m.trade_count) : "—";
}

// --------------------------------------------------------------------- //
// Pane: Trades                                                          //
// --------------------------------------------------------------------- //

function rMultiple(row) {
  // R = profit / |open_price - sl| * volume * pip_value, but we don't
  // have pip_value here. Approximate as profit-to-stop-distance ratio
  // when sl available. Fall back to "—".
  const open = Number(row.open_price);
  const sl = Number(row.sl);
  const profit = Number(row.profit);
  if (!Number.isFinite(open) || !Number.isFinite(sl) || sl === 0 || !Number.isFinite(profit)) return null;
  const dist = Math.abs(open - sl);
  if (dist <= 0) return null;
  // crude proxy: profit (USD) / stop-distance (price units) — comparable across same-symbol trades
  return profit / dist;
}

function sortRows(rows) {
  const dir = sortDir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    let av = a[sortKey];
    let bv = b[sortKey];
    if (sortKey === "r_multiple") { av = rMultiple(a); bv = rMultiple(b); }
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
    return String(av).localeCompare(String(bv)) * dir;
  });
}

function renderTrades(payload) {
  if (payload) lastTradesPayload = payload;
  const data = lastTradesPayload;
  const tbody = document.querySelector("#trades-table tbody");
  tbody.innerHTML = "";
  if (!data || data.status !== "ok" || !Array.isArray(data.rows)) return;
  const rows = sortRows(data.rows);
  for (const row of rows) {
    const tr = document.createElement("tr");
    const r = rMultiple(row);
    const cells = [
      row.ticket ?? "",
      row.symbol ?? "",
      row.type ?? "",
      fmtNum(row.volume, 2),
      fmtNum(row.open_price, 5),
      fmtNum(row.close_price, 5),
      fmtNum(row.profit, 2),
      r != null ? fmtNum(r, 2) : "—",
    ];
    cells.forEach((val, i) => {
      const td = document.createElement("td");
      td.textContent = val;
      if (i === 6) {
        const p = Number(row.profit);
        if (Number.isFinite(p) && p > 0) td.classList.add("profit-pos");
        if (Number.isFinite(p) && p < 0) td.classList.add("profit-neg");
      }
      tr.appendChild(td);
    });
    tbody.appendChild(tr);
  }
}

document.querySelectorAll("#trades-table th").forEach((th) => {
  th.addEventListener("click", () => {
    const k = th.dataset.key;
    if (!k) return;
    if (k === sortKey) {
      sortDir = sortDir === "asc" ? "desc" : "asc";
    } else {
      sortKey = k;
      sortDir = "asc";
    }
    renderTrades(null);
  });
});

$("filter-apply").addEventListener("click", () => poll());
$("filter-side").addEventListener("change", () => poll());

// --------------------------------------------------------------------- //
// Polling                                                               //
// --------------------------------------------------------------------- //

async function poll() {
  const side = $("filter-side").value || "ALL";
  const symbol = $("filter-symbol").value.trim();
  const tradesUrl = `/api/trades?limit=100&side=${encodeURIComponent(side)}${symbol ? `&symbol=${encodeURIComponent(symbol)}` : ""}`;

  const results = await Promise.allSettled([
    fetchJSON("/api/health"),
    fetchJSON("/api/equity"),
    fetchJSON(tradesUrl),
    fetchJSON("/api/metrics"),
  ]);

  const [h, e, t, m] = results;
  if (h.status === "fulfilled") renderHealth(h.value);
  if (e.status === "fulfilled") renderEquity(e.value);
  if (t.status === "fulfilled") renderTrades(t.value);
  if (m.status === "fulfilled") renderMetrics(m.value);

  const allOk = results.every((r) => r.status === "fulfilled");
  const anyDegraded = results.some((r) => r.status === "fulfilled" && r.value && r.value.status === "unavailable");
  const dot = $("poll-dot");
  setKlass(dot, allOk && !anyDegraded ? "ok" : (allOk ? "warn" : "bad"));
  $("last-poll").textContent = `last poll: ${new Date().toLocaleTimeString()}`;
}

poll();
setInterval(poll, POLL_MS);
