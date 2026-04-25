# Phase 0: Intake Validation

## Requirement Summary
Build `scripts/supervisor.py` — an unattended process supervisor for the MT5 paper-trading bot.

## Validation
- Problem statement: PRESENT (24/5 unattended forex operation)
- Scope: PRESENT (single file + tests)
- Acceptance criteria: PRESENT (9 explicit checkboxes)
- Constraints: PRESENT (stdlib only, no main.py changes, injectable subprocess)

## Scope Analysis
- Single bounded context (process supervision)
- One deliverable file + test file
- Estimated tasks: 5-8 atomic tasks
- `scope_warning`: false
- `dual_client`: false (Python CLI only)

## Confirmed Inputs
- Working dir: `/Users/ltmas/trading-bot-workspace/bot`
- Entry: `main.py --mode paper`
- Health output: `bridge_data/supervisor_health.json`
- Test runner: `python3 -m pytest tests/ -q`
- Existing baseline: 262 tests passing

## Decision
Proceed to Phase 0.5 (design gate). Inline spec is detailed enough to satisfy design gate without further Socratic refinement.
