# Ralph Pattern — Trading Bot Analysis
**Agent 3 Output | 2026-04-25**

---

## 1. Ralph Pattern — Trading Bot Mapping

Ralph is an autonomous agent loop where a shell script (`ralph.sh`) spawns fresh AI instances iteratively. Each instance sees only two files — `prd.json` and `progress.txt` — plus the codebase via git. This clean-context design prevents context bloat and forces each agent to work from documented knowledge rather than accumulated conversation state.

### Concept Mapping

| Ralph Concept | Generic Meaning | Trading Bot Mapping |
|---|---|---|
| `prd.json` | Ordered list of feature stories with pass/fail status | 10 trading bot stories from bridge client to live trading mode |
| `progress.txt` | Append-only learnings log, read at every iteration start | Bridge quirks (WebRequest timeout, heartbeat), data edge cases (5-digit quotes, cache TTL), strategy findings (ATR formula, guard thresholds) |
| `passes: true` | Story verified by quality checks (typecheck + tests) | Backtest guard passing: Sharpe > 1.5, DD < 5%, win rate > 45% + pytest suite green |
| Fresh context per iteration | Agent spawned with clean state | Each implementation agent reads only prd.json + progress.txt — no prior conversation context |
| `branchName` | Git branch for this feature set | `ralph/trading-bot-core` — all 10 stories on one branch |
| Stop condition `<promise>COMPLETE</promise>` | All stories pass | All 10 stories have `passes: true` |
| Archive | Old run preserved when branch changes | Previous research iteration archived before new parameter set begins |
| `CLAUDE.md` updates | Reusable patterns stored near source | Bridge quirks appended to `bot/core/bridge/CLAUDE.md`, risk rules to `bot/core/risk/CLAUDE.md` |

### Why This Fits the Trading Bot

The trading bot has strong story sequencing (you cannot build autoresearch without a working strategy, which requires a data fetcher, which requires a bridge client). Ralph's `depends_on` field enforces this ordering — the loop always picks the highest-priority story where all dependencies already pass. Bridge disconnections and guard failures are deterministic, recoverable errors — perfect for append-to-progress-txt resolution rather than human escalation.

---

## 2. prd.json — 10 Trading Bot Stories

See the actual file at: `/Users/ltmas/trading-bot-workspace/bot/ralph/prd.json`

Story summary with dependency chain:

```
US-001: MT5 HTTP Bridge Client        (no deps)
US-002: Paper Trading Mode            (deps: US-001)
US-003: Historical Data Fetcher       (deps: US-001)
US-004: EMA Crossover Strategy        (deps: US-003)
US-005: Risk Manager                  (deps: US-001, US-002)
US-006: Performance Tracker           (deps: US-002)
US-007: Autoresearch Loop             (deps: US-003, US-004, US-005, US-006)
US-008: Knowledge Base Integration    (deps: US-007)
US-009: Checkpoint/Recovery           (deps: US-002, US-006)
US-010: Live Trading Mode             (deps: US-001, US-002, US-005, US-009)
```

Each story has:
- `id` — US-001 through US-010
- `title` — short name
- `description` — user story format ("As the bot, I need X so that Y")
- `acceptanceCriteria` — concrete, testable list including the exact pytest command
- `priority` — integer 1–10 (drives iteration order)
- `passes` — false initially, set to true by the implementing agent
- `depends_on` — list of story ids that must be `passes: true` first
- `notes` — pre-loaded context the implementing agent needs (paths, gotchas, formulas)

---

## 3. progress.txt — Initial Template

See the actual file at: `/Users/ltmas/trading-bot-workspace/bot/ralph/progress.txt`

The initial content is pre-loaded with:
- `## Codebase Patterns` section at the top (read-first by every agent)
- Bridge architecture: HTTP REST on 192.168.64.1:8080, endpoint list, WebRequest quirks
- File-based IPC legacy note (do not use mt5_client.py for new code)
- Data characteristics: EURUSD/GBPUSD H1, 5-digit quotes, pip value
- Target metrics: Sharpe > 1.5, DD < 5%, win rate > 45%
- Risk framework: position sizing formula, Kelly fraction, drawdown multipliers
- Full project directory structure

Each subsequent iteration appends a dated entry using the format:
```
## YYYY-MM-DD HH:MM - US-00N
- What was implemented
- Files changed
- **Learnings for future iterations:**
  - Patterns discovered
  - Gotchas encountered
---
```

---

## 4. Ralph Iteration Protocol

### Per-Iteration Execution

**What the agent is given:**
1. `bot/ralph/CLAUDE.md` — the iteration instructions (piped to `claude --dangerously-skip-permissions --print`)
2. `bot/ralph/prd.json` — current pass/fail state of all 10 stories
3. `bot/ralph/progress.txt` — accumulated context from all prior iterations
4. The codebase via git (all files in `bot/`)

**What the agent must produce:**
1. Implementation of exactly one story (the highest-priority story where `passes: false` and all `depends_on` are `passes: true`)
2. A passing pytest suite for that story
3. A git commit: `feat: [US-00N] - [Story Title]`
4. `prd.json` updated: `"passes": true` for completed story
5. Appended entry in `progress.txt` with learnings
6. Updated `CLAUDE.md` in relevant subdirectory if reusable patterns discovered

**How `passes: true` is verified:**
- The backtest guard command from config: `python backtest/engine.py --guard`
- For non-strategy stories (US-001, US-002, US-005, US-006, US-009): `python -m pytest` for the story's test file
- For strategy/research stories (US-004, US-007): pytest + backtest guard must both pass
- Agent sets `passes: true` only after all acceptance criteria are met and quality checks are green

**What gets appended to progress.txt:**
- Date/time and story ID
- Files created/modified
- Learnings: bridge behaviours observed, MT5 edge cases, indicator formula corrections, performance surprises
- Any guard thresholds that were too loose or too tight

**When to escalate vs. retry:**
- Retry (max 3 attempts within one iteration): import errors, missing dependency, test fixture bug
- Escalate to next iteration: bridge not reachable (progress.txt note added, story left as `passes: false`)
- Hard stop (leave note in progress.txt, do not mark passes): backtest guard consistently fails after 3 param adjustments — needs human review of guard thresholds

---

## 5. Error Escalation Matrix

### Bridge Disconnection
- **Symptom**: `requests.exceptions.ConnectionError` or heartbeat age > 10s
- **Append to progress.txt**: `BRIDGE QUIRK: HTTP bridge at 192.168.64.1:8080 not reachable. Ensure UTM VM is running and EA is attached. MT5 must have 192.168.64.1 whitelisted in WebRequest settings.`
- **Story action**: Leave story `passes: false`. Do not attempt to implement bridge-dependent stories (US-002 through US-010).
- **Fix path**: Start UTM Windows VM → launch MT5 → attach EA → verify `/health` endpoint returns 200

### Backtest Guard Failure
- **Symptom**: `python backtest/engine.py --guard` exits non-zero, Sharpe < 1.5 or DD > 5%
- **Append to progress.txt**: Log the failing metric and current parameter set (fast_period, slow_period, atr_mult).
- **Story action**: Do NOT mark `passes: true`. Tighten parameters or widen guard window, retry backtest.
- **Fix path**: Consult research/knowledge-base.md for validated parameter ranges. Try fast=9/slow=21 (documented default). If 3 attempts all fail, append `GUARD BLOCKER: EMA crossover with H1 EURUSD fails guard consistently — knowledge base review needed`.

### OrderSend Rejection
- **Symptom**: Bridge /command endpoint returns `{"result": "ERROR", "code": 10019}` (not enough money) or `{"code": 10006}` (request rejected)
- **Append to progress.txt**: `MT5 ERROR CODE [code]: [description]. Common codes: 10019=insufficient margin, 10006=rejected by server, 10014=invalid volume (min 0.01 lots, step 0.01)`.
- **Story action**: For US-010 live broker tests, log the rejection code. Do not retry without fixing the root cause (volume below minimum, spread too wide).
- **Fix path**: Check lot size rounding (must be multiple of 0.01). Check margin requirements. For demo accounts, minimum volume is 0.01 standard lot.

### Data Gaps
- **Symptom**: OHLCV DataFrame has NaN rows or breaks in timestamp sequence
- **Append to progress.txt**: `DATA EDGE CASE: {symbol} {timeframe} has gaps at {timestamp_range}. Likely cause: weekend/holiday close. Drop NaN rows before indicator calculation. Never forward-fill OHLCV — use dropna().`
- **Story action**: Implement gap detection in HistoricalDataFetcher (US-003). Flag gaps in cache metadata.
- **Fix path**: Add `df.dropna(subset=['close']).reset_index(drop=True)` before returning from fetch(). Log gap count.

### MT5 WebRequest Timeout
- **Symptom**: EA-side timeout (MQL5 WebRequest returns -1 after 5s), bridge receives incomplete POST
- **Append to progress.txt**: `BRIDGE QUIRK: MT5 WebRequest has 5s hardcoded timeout. Bridge must respond within 4s. If processing is slow, return 200 immediately and queue the work.`
- **Fix path**: Bridge endpoints must respond with 200 within 4 seconds. Use background threads for disk writes.

### Import / Module Not Found
- **Symptom**: `ModuleNotFoundError` on pytest run
- **Retry action**: Check requirements.txt is installed (`pip install -r requirements.txt` in .venv). Verify import path uses `core.bridge.client` not `bridge.client`.
- **Append to progress.txt**: `DEV NOTE: Always run pytest from bot/ root with .venv active. Module root is bot/. Import as core.bridge.client, not bridge.client.`
