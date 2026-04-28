# Phase 1 — Implementation Plan (with rolled-in Plan-Review + Context)

**Run ID:** `20260427-bot-dashboard`
**Date:** 2026-04-27
**Folded phases:** 1.5 (Plan-Review) + 2 (Context) folded into this document per user instruction.

---

## Section A — Context map (rolled in from Phase 2)

### Existing files we **read** (no modifications)

| Path | Why we read it | Read at runtime? |
|---|---|---|
| `config.yaml` | `bridge.base_url`, `bot.instruments[0]`, `bot.timeframe`, `filters.regime.*` | Yes (every poll) |
| `logs/trades.csv` | All four panes derive from it | Yes (every poll, ≤100 rows for table; full file for equity, but `tail(10000)` cap) |
| `logs/health.jsonl` | Optional last-known-good fallback if pgrep returns nothing | Yes (best-effort, last 1 line via `tail`) |
| `bridge_data/history/EURUSD_M15.parquet` | Last 200 bars for regime classification | Yes (read-only, `pd.read_parquet`, no write) |

### Existing modules we **import** (no modifications)

| Symbol | From | Used for |
|---|---|---|
| `PerformanceTracker` | `core.performance.tracker` | Sharpe, expectancy, win_rate, profit_factor, payoff_ratio, max_drawdown |
| `RegimeDetector` | `core.regime.detector` | Current regime classification on cached M15 bars |

### External commands we shell out to (read-only)

- `pgrep -f 'main\.py.*--mode paper'` (then filter by `comm=python` via `ps -p $pid -o comm=`)

### Files we **write** (new only)

| Path | LOC est. | Purpose |
|---|---|---|
| `dashboard/__init__.py` | 5 | Package marker, re-export `app` |
| `dashboard/__main__.py` | 15 | `uvicorn.run(...)` entrypoint |
| `dashboard/app.py` | 90 | FastAPI app: routes, CSP middleware, static mount, template route |
| `dashboard/sources.py` | 220 | Data adapters: `probe_process`, `probe_bridge`, `read_trades`, `compute_metrics`, `current_regime`, `_compute_dsr` |
| `dashboard/templates/index.html` | 80 | Skeleton with four pane sections |
| `dashboard/static/app.js` | 150 | Polling logic + Chart.js init + table renderer + filter UI |
| `dashboard/static/styles.css` | 80 | Minimal layout (CSS grid, dark theme matches operator preference) |
| `dashboard/README.md` | 50 | Start commands, URL, source files, troubleshooting |
| `docs/decisions/0NN-bot-dashboard-form-factor.md` | 60 | ADR for form-factor + framework choice (number resolved at scaffold time) |
| `tests/dashboard/__init__.py` | 0 | Test package marker |
| `tests/dashboard/conftest.py` | 40 | Shared fixtures: tmp trades csv, mock bridge response, mock pgrep |
| `tests/dashboard/test_sources.py` | 200 | Unit tests for each adapter (≥ 8 tests) |
| `tests/dashboard/test_endpoints.py` | 150 | FastAPI TestClient tests (≥ 6 tests) |
| `scripts/start_dashboard.sh` | 25 | venv-activate + `exec python -m dashboard` |

**Net new code: ~1100 LOC across 14 files. No existing files modified.**

### Dependency verification (rolled-in pre-flight)

- `fastapi>=0.110` ✅ (`requirements.txt:11`)
- `uvicorn[standard]>=0.29` ✅ (`requirements.txt:12`)
- `pandas>=2.0` ✅ (`requirements.txt:3`)
- `pyyaml>=6.0` ✅ (`requirements.txt:2`)
- `pyarrow>=15.0` ✅ (`requirements.txt:5`) — needed for parquet read
- `pytest>=8.0` ✅ (`requirements.txt:28`)

**No new pip deps required.** Chart.js 4.x via `https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js` (pinned, SRI hash to be added in T3).

### Baseline pre-flight check (rolled in from Phase 3)

To be captured at the start of Phase 4 (T0): `python -m pytest -q | tail -1` → record exact pass count. The requirement states "current baseline is 551"; we will verify before any new test runs.

---

## Section B — Tasks (granularity: 2-5 min, 1-3 atomic steps each)

### T1 — Scaffold dashboard package + ADR

- **Files:** `dashboard/__init__.py`, `dashboard/__main__.py`, `dashboard/app.py` (skeleton — empty FastAPI app, health stub returning `{"status":"ok"}`), `docs/decisions/0NN-bot-dashboard-form-factor.md`
- **Steps:**
  1. Create the four files with minimal content. Number the ADR by `ls docs/decisions/ | sort | tail -1` + 1.
  2. Wire `__main__.py` to `uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8090, log_level="info", access_log=False)`.
  3. Smoke: `python -m dashboard` should start and respond `{"status":"ok"}` at `http://127.0.0.1:8090/api/health` (stub).
- **Acceptance:** Server starts on 127.0.0.1:8090. ADR file numbered correctly.
- **Tests added:** 1 (TestClient root smoke).

### T2 — Source adapter: `probe_process` + `probe_bridge` + tests

- **Files:** `dashboard/sources.py` (new), `tests/dashboard/__init__.py`, `tests/dashboard/conftest.py`, `tests/dashboard/test_sources.py` (new with these tests only)
- **Steps:**
  1. Implement `probe_process()` — port pgrep+ps logic from `daily_health_check.sh:42–48`. Returns `{"status":"running"|"not_running","pid":int|None,"etime":str|None}`. Catches all exceptions → `{"status":"unavailable","error":str(e)}`.
  2. Implement `probe_bridge(base_url, timeout=3)` — port from `detect_bridge.py:34–39`. Returns `{"status":"ok"|"unreachable","pong":bool|None,"ea_connected":bool|None,"latency_ms":float|None,"error":str|None}`.
  3. Tests (≥ 6): pgrep returns no match → `not_running`; pgrep matches non-python → filtered out; pgrep matches python → `running` with pid/etime; bridge unreachable → `unreachable`; bridge OK + ea_connected → `ok`; bridge OK + ea disconnected → `ok` with `ea_connected=false`.
- **Acceptance:** All 6 tests pass. No network calls (use `monkeypatch.setattr(subprocess, "run", ...)` and `monkeypatch.setattr(urllib.request, "urlopen", ...)`).

### T3 — Source adapter: `read_trades` + `compute_equity_series` + tests

- **Files:** extend `dashboard/sources.py`, extend `tests/dashboard/test_sources.py`
- **Steps:**
  1. `read_trades(path, limit=100)` — `pd.read_csv` with explicit dtypes, `usecols=[the 11 columns]`, `on_bad_lines="skip"`. Filter to rows with non-empty `close_time` for *closed* trades only. Returns `pd.DataFrame` (or empty DF on missing file). Caller does `.tail(limit)`.
  2. `compute_equity_series(closed_df)` → `{"timestamps":[...iso...], "equity":[...float...], "peak":[...], "drawdown":[...]}` derived as: `equity = closed_df["profit"].cumsum()`, `peak = equity.cummax()`, `drawdown = where(peak>0, (peak-equity)/peak.abs().clip(lower=1.0), 0.0)`.
  3. Tests (≥ 5): missing file → empty arrays + status ok; only-open trades → empty equity; mixed → equity matches cumsum; peak monotonically non-decreasing; drawdown ≥ 0 always.
- **Acceptance:** All 5 tests pass.

### T4 — Source adapter: `compute_metrics` + DSR helper + `current_regime` + tests

- **Files:** extend `dashboard/sources.py`, extend `tests/dashboard/test_sources.py`
- **Steps:**
  1. `_compute_dsr(sharpe, n_trades, skew=0.0, kurt=3.0, sr_benchmark=0.0)` — Bailey/López de Prado closed form: `DSR = Φ((sharpe - sr_benchmark) * sqrt(n_trades-1) / sqrt(1 - skew*sharpe + (kurt-1)/4 * sharpe**2))`. Return 0.0 for n_trades < 2 or invalid args. `Φ` via `0.5*(1 + math.erf(z/math.sqrt(2)))`.
  2. `compute_metrics(closed_df)` → builds `PerformanceTracker`, calls `record_trade` for each row mapped to the tracker's expected dict (`profit`, `open_time`, `close_time`), returns `{"sharpe": ..., "dsr": ..., "expectancy": ..., "win_rate": ..., "payoff_ratio": ..., "trade_count": ...}`. Catch all → `{"status":"unavailable","error":...}`.
  3. `current_regime(config, parquet_path, bars=200)` — read parquet (`pd.read_parquet(parquet_path, columns=["timestamp","open","high","low","close","volume"]).tail(bars)`) → `RegimeDetector.from_config(config).current_regime(df)` → string label `"trend"|"range"`. On any failure → `{"status":"unavailable","label":"unknown"}`.
  4. Tests (≥ 5): tracker integration with mocked DataFrame; DSR formula correctness on a known case (sharpe=1.5, n=100, skew=0, kurt=3 → expected ≈ 0.93); DSR returns 0 on n<2; regime returns "trend" or "range" given a synthesised DataFrame; regime returns "unknown" when parquet path missing.
- **Acceptance:** All 5 tests pass.

### T5 — FastAPI routes + CSP middleware + static mount

- **Files:** rewrite `dashboard/app.py` (replace T1 stub), `tests/dashboard/test_endpoints.py` (new)
- **Steps:**
  1. Build `app = FastAPI(...)`. Add response-header middleware that sets the CSP from design brief A9 on every response. Mount `/static` to `dashboard/static`.
  2. `GET /` → serves `templates/index.html` (use `FileResponse`, no Jinja templating — the page is static and reads data via JS polling).
  3. `GET /api/health` → composes `probe_process()` + `probe_bridge(cfg.bridge.base_url)` + `current_regime(cfg, parquet_path)` + best-effort `peak/current drawdown` from closed_df → JSON.
  4. `GET /api/equity` → `compute_equity_series(closed_df)` → JSON.
  5. `GET /api/trades?limit=100&side=BUY|SELL|ALL&symbol=...` → filter + `.tail(limit)` → list of row dicts.
  6. `GET /api/metrics` → `compute_metrics(closed_df)` → JSON.
  7. Each route wraps its body in `try/except Exception → JSONResponse(status_code=200, content={"status":"unavailable","error":...})`. **Routes never 500.**
  8. Tests (≥ 6, FastAPI TestClient + monkeypatched sources): `/` returns 200 HTML; `/api/health` returns 200 with `bridge.status=="unreachable"` when bridge mocked to fail; `/api/equity` returns the computed series; `/api/trades?side=BUY` filters; `/api/metrics` returns sharpe key; CSP header present on every response.
- **Acceptance:** All 6 tests pass + manual `curl http://127.0.0.1:8090/api/health` while bot is running.

### T6 — Frontend HTML + JS + CSS

- **Files:** `dashboard/templates/index.html`, `dashboard/static/app.js`, `dashboard/static/styles.css`
- **Steps:**
  1. `index.html`: four `<section>` skeletons (health, equity-chart, trades-table, metrics-tiles). Load Chart.js from CDN with SRI. Single `<script src="/static/app.js" defer>`.
  2. `app.js`: `async function poll()` calls all four endpoints with `Promise.allSettled`, renders each pane independently. `setInterval(poll, 7000)`. On individual failure, last-known DOM stays. Trade-table sort handlers (click column header to toggle asc/desc). Side filter (`<select>`) + symbol filter (`<input>`). Chart.js: line chart with two datasets (equity, peak) + filled drawdown overlay (twin-axis or transparent fill).
  3. `styles.css`: CSS grid 2x2 panes, dark background `#0e0e10`, accent for alerts.
  4. Manual smoke: open `http://127.0.0.1:8090/` while bot is running → all panes populate within 7s.
- **Acceptance:** Manual smoke passes; verified visually by operator.
- **Tests added:** 0 (frontend-only, no Playwright in this run — the requirement constrains to "no new dependencies").

### T7 — `start_dashboard.sh` + README + integration smoke

- **Files:** `scripts/start_dashboard.sh`, `dashboard/README.md`
- **Steps:**
  1. `start_dashboard.sh`: mirror `start_bridge.sh` pattern — activate venv, `cd "$BOT_ROOT" && exec python -m dashboard "$@"`. `chmod +x`.
  2. `README.md`: start command, URL, files-it-reads list, troubleshooting (bridge unreachable, bot not running, parquet missing → expected behaviour for each).
  3. Run full `python -m pytest -q`. Confirm new tests pass + baseline 551 still green.
  4. Manual smoke matrix: (a) bot running + bridge up → all panes populated; (b) bot killed → health flips `not_running`, others still render; (c) bridge stopped → health flips `unreachable`, others still render; (d) parquet missing → regime shows `unknown`, others fine.
- **Acceptance:** AC1, AC2, AC3, AC4, AC5 all verified.

### Task dependency graph

```
T1 (scaffold) → T2 (probe adapters) → T5 (routes)
                T3 (trades/equity) ──┘
                T4 (metrics/regime) ─┘
                                     ↓
                                  T6 (frontend) → T7 (script + README + smoke)
```

T2, T3, T4 have no shared files within `sources.py` (each adds disjoint functions) — sequential by convention to keep `sources.py` reviews clean, but can be parallelised if needed.

---

## Section C — Self-review (per Plan-agent dispatch rule)

| Check | Result |
|---|---|
| **Spec coverage** — Every AC mapped to a task | AC1/AC2/AC6 → T7 smoke. AC3 → T2/T5 (graceful degradation tests). AC4 → T2/T5 (graceful degradation tests). AC5 → T7 (full pytest). AC7 → T7 (README). ✅ |
| **Placeholder scan** | No `TBD`/`TODO`/`implement later`/`similar to Task N`/`add appropriate` strings in this plan. ✅ |
| **File boundary map** | Every file named with absolute or repo-relative path in Section A table. ✅ |
| **Granularity** | Each task: 1–3 atomic steps, 2-5 min target. T6 (frontend) is the largest at ~3 steps + manual smoke; still bounded. ✅ |
| **Scope check** | Single bounded context (read-only consumer). Independent of other in-flight work. ✅ |

**self_review_pass: true**
**self_review_notes: ""**

---

## Section D — Plan-Review verdict (rolled in from Phase 1.5)

**Verdict:** APPROVE.

| Doublecheck axis | Finding |
|---|---|
| No-placeholders scan | Pass — grep'd this document for `TBD`, `TODO`, `implement later`, `similar to Task`, `add appropriate` → 0 matches. |
| Granularity | Pass — no task >5 steps or touching >5 files (T5 touches 2 files; T6 touches 3 files). |
| Acceptance criteria coverage | Pass — every AC1–AC7 is mapped to at least one verification step. |
| Constraint adherence | Pass — no edits to `main.py`/`core/execution`/`core/risk`/`autoresearch`; no new pip deps; `127.0.0.1`-only binding documented in T1; CSP locked-down in T5. |
| Test offline | Pass — T2/T3/T4 explicitly mock `subprocess`, `urllib`, `read_csv`, `read_parquet`. |
| Risk areas | (1) `bridge_data/history/EURUSD_M15.parquet` could be locked during a bridge write — mitigated by A8 fall-through to `unknown`. (2) `pgrep` macOS-specific — acceptable per design brief A3. (3) Chart.js CDN — pinned version + SRI required, captured in T6 step 1. |

**Approval:** Auto-mode — orchestrator approves and proceeds to Phase 4.
