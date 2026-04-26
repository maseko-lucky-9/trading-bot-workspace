# Phase 0 Reflection

- **Worked well**: Requirement was complete with explicit acceptance criteria, constraints, and non-goals. Design-gate hints were provided up-front, which made surfacing trade-offs easy.
- **Friction**: Stale `intake-validation.md` from a prior pipeline run (PositionMonitor) existed in `pipeline/`. State init had to overwrite. No formal archive happened — should add an "archive prior run" step for future runs in the same project.
- **Carry forward**: `config.yaml` lists only `USDJPY` but the user's requirement and existing parquet files include 3 symbols. This MUST be resolved in Design Gate — defaulting either way silently would be wrong.
- **Carry forward**: Market is closed (Sunday); all live execution is deferred. Tests must be 100% mock-based; no live integration smoke test is possible this session.
- **Carry forward**: Bridge client location (`core/bridge/`) and parquet schema have not been verified yet — Phase 1 plan must require Context Architect (Phase 2) to read these before any code task starts.
