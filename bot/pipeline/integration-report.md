# Integration Report

## Test execution
**Status:** NOT EXECUTED IN-SESSION (no Bash tool available to this orchestrator instance)

The user must run:
```
cd /Users/ltmas/trading-bot-workspace/bot
python3 -m pytest tests/ -q
```
Expected result: 262 prior tests + ≥10 new supervisor tests, all passing.

## Static analysis (manual trace)
| Test | Expected behaviour | Trace verdict |
|---|---|---|
| `test_market_closed_saturday_noon` | wd=5 → False | PASS |
| `test_market_open_monday_morning` | wd=0 → True | PASS |
| `test_market_open_sunday_after_22` | wd=6, h=22 → True; h=21 → False | PASS |
| `test_market_closed_friday_after_21` | wd=4, h=22 → False; h=20 → True | PASS |
| `test_compute_backoff_doubles_and_caps` | 30/60/120/240/480/900/900 | PASS |
| `test_health_file_written_within_5s` | First write happens before sleep(interval) → < 1s | PASS |
| `test_max_restarts_halts_loop` | 3 spawns then break (count 0→1→2 hits limit) | PASS |
| `test_backoff_resets_after_long_uptime` | uptime>3600 → count stays 0; supervisor stopped after 5 spawns | PASS |
| `test_backoff_increments_on_short_uptime` | uptime<3600 → count increments to 3, halts | PASS |
| `test_request_stop_terminates_child_and_returns_zero` | request_stop → terminate child → exit 0 | PASS |
| `test_sigkill_when_child_ignores_sigterm` | grace=0.3s exceeded → kill called | PASS |
| `test_dry_run_exits_zero_and_prints_command` | prints `main.py --mode paper`, returns 0 | PASS |
| `test_arg_parser_accepts_all_flags` | argparse ns with all flags | PASS |
| `test_market_gate_blocks_spawn_when_closed` | Saturday clock → no spawn within 0.5s | PASS |

14 tests written, all expected to pass per static trace.

## Risks
- Threading + timing tests can be flaky under heavy CI load. The shortest timeout is 0.3s (sigkill grace) which should be safe.
- `_install_signal_handlers` is wrapped in `try/except (ValueError, OSError)` so background-thread invocation in tests is harmless.
