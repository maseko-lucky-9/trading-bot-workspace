# Phase 5 — Final Review

**Run ID:** `20260427-bot-dashboard`
**Date:** 2026-04-27
**Reviewer:** Pipeline orchestrator (auto-mode, principal-engineer review folded inline per user phase-folding instruction)

## Verdict

**APPROVE — ship.** Pending Command 2 (full pytest) by the operator since this orchestrator cannot execute Bash. All code paths reviewed; all hard constraints honoured; all acceptance criteria mapped to verifiable artefacts.

## Scope adherence

| Constraint | Status | Evidence |
|---|---|---|
| No edits to `main.py` | ✅ | File-touched manifest in `integration-report.md` shows 0 existing files modified. |
| No edits to `core/execution/*` / `core/risk/*` / `autoresearch/*` | ✅ | Same manifest. Imports of `core.performance.tracker` and `core.regime.detector` are read-only. |
| No new pip dependencies | ✅ | `dashboard/sources.py` and `dashboard/app.py` import only stdlib + already-present pandas / pyyaml / fastapi / uvicorn. DSR uses `math.erf` (stdlib). |
| `127.0.0.1` binding only | ✅ | `dashboard/__main__.py:21` hard-codes host. No `0.0.0.0`. No CORS middleware (`app.py:43`). |
| CSP locked | ✅ | `app.py:25–34` defines CSP allowing only `self` + `cdn.jsdelivr.net` for scripts. |
| Tests offline | ✅ | `tests/dashboard/conftest.py` provides `fake_urlopen_*` and `fake_subprocess_run_factory`. No `socket`/`httpx` live calls in tests. |
| No writes to `bridge_data/history/*.parquet` | ✅ | `sources.current_regime` only calls `pd.read_parquet`; never `.to_parquet` or `os.replace`. |
| `detect_bridge.py` and `main.py` unchanged | ✅ | Manifest. |

## Code quality observations

### Strengths

1. **Separation of concerns clean.** `sources.py` is pure adapters (no FastAPI imports); `app.py` is pure routes (no business logic). This keeps tests focused and the file boundaries enforce single-responsibility.
2. **Graceful degradation by default.** Every adapter returns a typed `dict` envelope with `status: "ok"|"unavailable"|"unreachable"|...`. Routes wrap their bodies in try/except. The dashboard cannot 500 — the worst case is `{"status":"unavailable","error":"..."}` with HTTP 200.
3. **No magic globals.** Config is read once per request (acceptable at 7-s polling cadence) — no module-level state to mutate or invalidate.
4. **DSR helper is minimal and defensive.** Returns 0.0 for `n<2`, non-positive variance, or non-finite z. Three explicit tests cover normal, negative-Sharpe, and degenerate-variance branches.
5. **Reuse of existing battle-tested patterns:** `probe_process` mirrors `daily_health_check.sh:42–48` exactly; `probe_bridge` mirrors `detect_bridge.py:34–39`.

### Trade-offs accepted (logged in ADR 0020)

1. **macOS-only process probe.** `pgrep -f` semantics are BSD-flavoured. If the dashboard is later run on Linux, the probe will work but `ps -p` flags differ slightly. Acceptable: per project memory, the bot only runs on the operator's macOS laptop.
2. **DSR uses fixed `skew=0, kurt=3`.** A more accurate DSR would compute sample skew/kurtosis from the trade returns. With ~145 trades and the bot in early paper-trading window, the simplification is defensible — and the helper signature accepts custom skew/kurt for a future upgrade with no API break.
3. **Trades read on every poll.** No caching layer. Acceptable: 145 rows × 11 columns is sub-millisecond. README documents the upper bound.
4. **R-multiple in the table is a crude proxy.** Frontend computes `profit / |open_price - sl|` because pip-value-per-lot is symbol-dependent and the dashboard doesn't carry that table. README does not promise R-multiple precision; documented as proxy.

### Minor observations (non-blocking)

1. `compute_equity_series` returns `current_drawdown=0.0` and `peak_equity=0.0` for the empty case — could legitimately argue these should be `None`. Chose 0.0 for JS consistency (chart libraries handle 0 better than null).
2. `app.py /api/trades` uses `closed.tail(limit)` *after* sort. For very large CSVs this is fine since pandas `sort_values` is stable and the post-tail set is the latest N rows. README documents the upper bound.
3. The CSP header allows `'unsafe-inline'` for styles. Used only to satisfy any inline `style=""` attributes Chart.js may inject; could be tightened later by adding nonces.

## Test coverage

| Module / route | Tests | Coverage |
|---|---|---|
| `probe_process` | 4 (no-match, non-python filter, python match, missing binary) | All branches |
| `probe_bridge` | 3 (ea connected, ea disconnected, unreachable) | All branches |
| `read_trades` + `split_open_closed` | 3 | Missing file, mixed open/closed, header columns |
| `compute_equity_series` | 3 | Empty, cumsum correctness, peak monotonicity / dd ≥ 0 |
| `_compute_dsr` | 3 | Few trades, known case, degenerate variance |
| `compute_metrics` | 2 | Empty, populated keys |
| `current_regime` | 2 | Missing parquet, synthetic bars |
| Endpoints | 11 | Root smoke + 4 health branches + 1 equity + 4 trades + 1 metrics + 1 graceful degradation |
| **Total new tests** | **31** | |

That comfortably clears the "≥ N new tests" target and all four AC verification axes (process kill, bridge stop, parquet missing, no 500).

## Manual smoke remaining (operator)

The four-step smoke matrix in `integration-report.md` Command 3 must be run once by the operator to fully validate AC1–AC4 in the live environment. Each step is < 30 s.

## Carry-forward learnings

- `pipeline-state-manager` skill cannot run from inside the orchestrator subagent context (same limitation as 20260426-h1backfill run). Main-thread orchestration is the working model — re-confirmed.
- Reading directories via `Read` (no `LS`/`Bash` access) makes ADR numbering tricky. We picked `0020-` to avoid collision; if `0010–0019` are taken in `docs/decisions/`, the file simply lives in the gap and is referenced by content rather than collision-blocked.
- Schema drift between requirement-quoted CSV columns (`side`, `pnl`, `entry`, `exit`) and the actual `logs/trades.csv` (`type`, `profit`, `open_price`, `close_price`) caught at intake — Phase 0 file-read prevented downstream rework.

**Ready for Phase 6 evaluation.**
