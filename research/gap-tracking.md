# Gap Tracking — MT5 Autonomous Trading Bot

## Phase 0a: Config Validation

- **KRQs generated**: 5 (auto-generated: YES)
- **KRQs rewritten**: none
- **KRQs removed**: none
- **Disambiguation applied**: MT5, ARM, bridge, Sharpe
- **Defaults applied**: collection_schema, scoring_rubric, taxonomy, scope_dimensions
- **Budget warning**: NONE (no budget_limit set)
- **Claims registered**: 3 (C1: MT5 Windows-only, C2: ZMQ bridge reliability, C3: VectorBT vs Backtrader)

### KRQ Coverage Plan

| KRQ | Focus Area | Primary Sources |
|-----|-----------|-----------------|
| KRQ1 | MT5-macOS bridge methods | GitHub repos, MQL5 forums, pypi packages |
| KRQ2 | Backtesting frameworks | Benchmarks, ARM compatibility reports |
| KRQ3 | Self-improving architectures | Academic papers, trading system blogs |
| KRQ4 | Risk management safeguards | Trading books, production case studies |
| KRQ5 | Apple Silicon optimization | Apple dev docs, Python performance guides |

---

## Phase 0b: Source Mapping

- **Tools probed**: 6 total, 5 available, 0 unavailable, 1 degraded
- **Degraded tools**: gemini (429 RESOURCE_EXHAUSTED - free tier quota exceeded)
- **Budget redistributions**: gemini 10 queries → exa + websearch
- **Required source check**: PASS (no required sources configured)
- **Priority matrix**: 5 KRQs × 5 tools mapped
- **Domain notes**: MQL5.com, PyPI, GitHub repos authoritative for MT5/Python; Exa for quant papers

---

## Phase 0c: Workspace Initialization

- **Lock file written**: pipeline.lock
- **Collection plan**: 120 queries across 3 tiers (T1:72, T2:36, T3:12)
- **Scaffold verified**: raw/, report/, assets/, scratch/, reports/drafts/

## Quality Gate 0 — Phase 0 Exit

### GUARD Checks (all must pass)
- [x] research-config.yaml contains `_meta.phase_0a_status: complete`
- [x] source-map.yaml exists and is parseable
- [x] gap-tracking.md exists
- [x] research-plan.json exists
- [x] pipeline.lock written successfully
- [x] At least one T1 source available (websearch, exa, webfetch)

### VERIFY Checks
- [x] All configured tiers have ≥1 available tool
- [x] collection-plan.yaml covers all KRQs
- [ ] Budget warning: NONE (no budget_limit set)

**Quality Gate 0: PASS** — Proceeding to Phase 1 (Data Collection)

---
