# Context Map

## Primary files (will be created)
- `scripts/supervisor.py` — supervisor module (new)
- `tests/test_supervisor.py` — unit tests (new)

## Read-only context
- `main.py` — entry point invoked by supervisor (read-only, do NOT modify per constraint)
- `config.yaml` — bot config (untouched by supervisor)
- `tests/` — existing 262-test suite (must remain green)

## Generated artifact at runtime
- `bridge_data/supervisor_health.json` — health status, written every 30s

## Dependencies
- Stdlib only: `subprocess`, `signal`, `json`, `datetime`, `time`, `threading`, `argparse`, `os`, `sys`, `pathlib`, `tempfile` (for atomic write)
- Test deps: `pytest` (existing), `unittest.mock` (stdlib)

## Patterns
- Inject `spawn_fn` and `clock_fn` via constructor for testability (no real subprocess in unit tests)
- Atomic file write: `tempfile.NamedTemporaryFile` in target dir + `os.replace`
- Background thread + `threading.Event` for periodic health writes

## No unknowns. Proceed.
