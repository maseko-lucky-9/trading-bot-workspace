# Design Brief — Local Web Dashboard for MT5 Bot

**Run ID:** `20260427-bot-dashboard`
**Date:** 2026-04-27
**Status:** APPROVED (form factor pre-locked by user; auto-mode confirms internal architecture)

## Goal (one sentence)

A single-process FastAPI app on `127.0.0.1:8090` serving a static HTML page + four read-only JSON endpoints that pull live state from existing bot artifacts (process table, bridge `/ping`, `logs/trades.csv`, in-memory regime classifier on cached bars).

## Pre-locked decisions (user, in requirement)

1. **FastAPI + vanilla HTML + Chart.js (CDN)**, no node/npm.
2. **Polling** at 5–10s, no websockets.
3. **127.0.0.1:8090 only**, no auth, no TLS, no LAN.
4. **Single Python module** under `bot/dashboard/`.
5. **Read-only consumption** of existing artifacts; no edits to bot runtime path.

## Architectural choices (decided this phase)

| # | Decision | Trade-offs accepted |
|---|---|---|
| A1 | **Module layout**: `bot/dashboard/__init__.py`, `bot/dashboard/__main__.py` (uvicorn entrypoint), `bot/dashboard/app.py` (FastAPI app + routes), `bot/dashboard/sources.py` (data adapters: process probe, bridge probe, trades reader, metrics, regime), `bot/dashboard/templates/index.html`, `bot/dashboard/static/app.js`, `bot/dashboard/static/styles.css`. | More files than the requirement's "single Python module" hint, but the split keeps `app.py` thin (routes only) and `sources.py` 100% mockable for offline tests. Net surface stays small (~6 files). |
| A2 | **Polling endpoints**: `GET /api/health`, `GET /api/equity`, `GET /api/trades`, `GET /api/metrics`. Each independent — failure of one does not 500 the others. | Four round-trips per refresh vs one aggregated `/api/snapshot`. Chosen for graceful degradation (browser keeps last-known on individual failure) and easier unit tests (one fixture per endpoint). |
| A3 | **Process probe**: reuse the exact pgrep+ps filter from `scripts/daily_health_check.sh` lines 42–48, ported to Python via `subprocess.run(["pgrep", "-f", "main\\.py.*--mode paper"])` then filter by `comm=python` via `ps -p $pid -o comm=`. **No new dependencies.** | Couples to macOS pgrep semantics. Fine — the bot only runs on the operator's macOS laptop (per project memory `Memory/wiki/feedback/repo_directory.md`). |
| A4 | **Bridge probe**: reuse pattern from `scripts/detect_bridge.py` (urllib, 5s timeout, return `{}` on failure). Read `bridge.base_url` from `config.yaml`. Wrap in 3s timeout for the dashboard endpoint so the page renders fast even when bridge is unreachable. | Slightly tighter timeout than detect_bridge.py's 5s — dashboard prioritises responsiveness, detect_bridge.py prioritises completeness. |
| A5 | **Trades reader**: `pandas.read_csv(path, usecols=..., dtype=..., on_bad_lines="skip")` with `tail(N)` after read, where `N=100` for the table and `N=10000` for equity (the file has 145 rows today; ceiling avoids reading future-million-row files). Re-read on each request — no caching layer (file is small, the write rate is per-trade, ETag complexity not worth it). | Re-read on every poll. Acceptable: 145 rows × 11 columns is negligible. Document the upper bound in README. |
| A6 | **Equity series**: cumulative sum of `profit` column, restricted to *closed* rows (`close_time` non-empty). Peak via `.cummax()`. Drawdown = `(peak - equity) / peak` where peak > 0 else absolute. Returned as two arrays + a timestamp array — Chart.js consumes directly. | Open positions don't contribute to equity (matches `tracker.py`'s convention). The trades.csv contains both open-event rows and matching close-event rows for the same ticket; we filter to rows with non-empty `close_time` to avoid double-counting. |
| A7 | **Metrics**: import `core.performance.tracker.PerformanceTracker`, feed it the closed-trade dicts, call `.summary()`. Add a *separate* `_compute_dsr(sharpe, n_trades, skew=0, kurt=3)` helper local to `sources.py` using the Bailey/López de Prado closed form (no SciPy needed — `math.erf` is in stdlib). | Re-derive DSR locally rather than touching `tracker.py` (constraint: no edits to existing bot files). Acceptable code duplication for one helper. |
| A8 | **Regime**: import `core.regime.detector.RegimeDetector`, build with `RegimeDetector.from_config(yaml.safe_load(config.yaml))`, run `detect()` on the last 200 bars of `bridge_data/history/EURUSD_M15.parquet` **read-only** (`pd.read_parquet`, never write). If parquet absent or bridge_data is locked: return `regime: "unknown"` rather than 500. | Reading the parquet that the bridge writes into is a soft race — pandas/pyarrow read is safe across the bridge's atomic-rename writes (verified by the H1 backfill module which uses the same `os.replace` pattern). |
| A9 | **CSP/CORS**: response header `Content-Security-Policy: default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'`. **No CORS middleware** (everything is same-origin). Bind uvicorn explicitly to `127.0.0.1` — never `0.0.0.0`. | `unsafe-inline` for styles only (small inline CSS for layout). Chart.js loaded from `cdn.jsdelivr.net` over HTTPS — pinned subdomain, no wildcard. |
| A10 | **Graceful degradation**: every adapter in `sources.py` returns a typed `dict` with explicit `status: "ok" \| "unavailable"` and `error: str \| None`. Endpoints never raise — `try/except Exception → return {"status":"unavailable", ...}`. The HTML renders `—` for any field whose envelope is unavailable. | Hides programming errors behind `unavailable`. Mitigation: server logs the exception traceback (loguru, already in requirements.txt). |
| A11 | **Tests**: `bot/tests/dashboard/test_sources.py` (unit, mocks subprocess + httpx + read_csv) and `bot/tests/dashboard/test_endpoints.py` (FastAPI `TestClient`). Target: ≥ 12 new tests. **Zero network calls.** Use `monkeypatch` + `respx` (already a transitive dep of httpx? Check Phase 4) — fallback to `unittest.mock.patch` if not. | Adds modest test surface but mandatory for the AC. |
| A12 | **Entrypoint**: `python -m dashboard` resolves via `bot/dashboard/__main__.py` calling `uvicorn.run("dashboard.app:app", host="127.0.0.1", port=8090, log_level="info", access_log=False)`. Working directory MUST be `bot/` when invoking. README documents this. Also provide `scripts/start_dashboard.sh` per the requirement. | None significant. |

## Trade-off table — option set considered for A1 (module layout)

| Option | Pros | Cons | Decision |
|---|---|---|---|
| Single `bot/dashboard.py` (literal) | Smallest surface | Mixing template strings + adapters + routes makes it untestable | ❌ |
| `bot/dashboard/` package as A1 above | Adapters mockable in isolation, templates separable, ~150 LOC per file | Six files instead of one | ✅ |
| `bot/dashboard/` + monolithic adapter file | Slight reduction in file count | No real benefit; the adapters are heterogeneous (process / http / csv / parquet) | ❌ |

## Open questions

None. All form-factor decisions were locked by the user; A1–A12 are routine implementation choices the orchestrator decides under auto-mode.

## ADR

This brief produces a corresponding ADR at `docs/decisions/0NN-bot-dashboard-form-factor.md` (numbering set during Phase 4 T1 alongside scaffolding). The ADR captures decisions A1, A2, A9 (the externally-visible architectural ones); A3–A8, A10–A12 are implementation detail and live only in this brief.

## Approval

Auto-mode active. Form factor pre-approved by user in requirement. Internal decisions (A1–A12) approved by orchestrator under auto-mode mandate. Proceed to Phase 1.
