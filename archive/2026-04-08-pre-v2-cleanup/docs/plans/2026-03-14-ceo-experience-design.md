# CEO Experience Design

**Date**: 2026-03-14
**Status**: Approved
**Context**: Post-Experiment #003 — infrastructure validated, product surface missing

---

## Core Principle

The lead agent is the product. Telegram and dashboard are surfaces the lead uses to communicate with the CEO.

The CEO delegated to their lead agent, not to a task board. They want to know:
- Does anyone need me right now?
- Any surprises or problems?
- Any decisions that genuinely require my judgment?

Everything else is the lead's problem.

---

## Surface 1: Telegram (Primary — Push)

The CEO talks to their lead bot. The bot handles orchestration. The CEO only sees:

### Goal Input
CEO gives a natural language goal. Lead confirms and goes silent.

### Silence (Normal State)
Workers are executing in rooms. CEO does other things. Lead handles all orchestration decisions autonomously.

### Decision Escalation (Rare)
Lead only escalates when it genuinely needs CEO context. Format:
1. Explain the situation briefly
2. Explain why the lead can't decide alone
3. Give the lead's recommendation
4. Structured choices + "or type what you're thinking..." (generative UI — always an open text option)

What the lead does NOT escalate:
- Operational decisions (which room, which agent, restart a bridge)
- Format decisions (how to assemble, what structure)
- Recovery actions (auto-restart, auto-replace)

What the lead DOES escalate:
- Decisions requiring CEO's context (audience, priorities, relationships)
- Direction changes affecting the whole mission
- Genuinely ambiguous situations where the lead lacks information

### Results Delivered
Lead synthesizes all room outcomes into one assembled deliverable. Sent in-chat with a dashboard link for details.

---

## Surface 2: Dashboard (Secondary — Pull Check-in)

URL: `clawroom.cc/m/{mission_id}`

### State 1: All Quiet (Nothing Needs CEO)
```
All quiet
3 tasks in progress — est. ~8 min

Lead: @singularitygz_bot
Started: 2 min ago
```
One glance. Close the phone.

### State 2: Decision Needed
```
Your lead wants to discuss something.
→ Open in Telegram
```
Decisions happen via conversation with the lead, not dashboard buttons.

### State 3: Results Ready
Assembled deliverable front and center. Execution details (turns, runtimes, recovery events, certification) available via expandable section for trust-building.

### Design Principles
- Mobile-first (iPhone)
- Boring on purpose — the result is interesting, not the process
- 3 states only: quiet / needs-you / done
- No task board, no progress bars, no worker management
- Execution details expandable (not primary)

---

## What This Is NOT (vs Competitors)

| Aspect | Paperclip | Symphony | ClawRoom |
|--------|-----------|----------|----------|
| Primary interface | Dashboard | Linear board | Lead agent |
| CEO manages tasks | Yes | Yes | No — lead manages |
| CEO sees workers | Yes | Yes | Only in expanded details |
| Results format | Per-task | Per-PR | Assembled deliverable |
| Default state | Task board | Ticket list | "All quiet" |
| Product surface | Dashboard IS product | Board IS product | Lead agent IS product |

---

## Artifact Flow

```
CEO → goal → Lead agent (Telegram)
Lead → decomposes → N rooms with required_fields
Lead → wakes workers via runnerd
Workers → fill required_fields → rooms close
Lead → fetches room results → synthesizes deliverable
Lead → sends deliverable to CEO (Telegram)
Dashboard → shows same result + execution metadata on demand
```

---

## Technical Implementation

### Dashboard (apps/monitor/)
- New URL route: `?mission_id=X`
- Mission view with 3 states: quiet / decision / results
- No MissionDO dependency — client-side aggregation from rooms sharing a mission_id
- Mobile-first layout
- Expandable execution details section

### Lead Agent (skills/clawroom-lead/)
- Autonomy threshold: decide most things, escalate with context + recommendation
- Result synthesis: fetch room results, assemble one deliverable
- Decision escalation format: situation, why asking, recommendation, choices + free text

### Bridge Prompts (skills/clawroom/SKILL.md)
- "Never defer. Never say 'see you later.' Always act now."
- "Fill required_fields before closing. This is your primary job."

### Key Files
- `apps/monitor/src/main.js` — mission view (currently stub at lines 1188-1239)
- `apps/monitor/src/css/style.css` — mission card styles
- `apps/monitor/index.html` — mission view HTML
- `skills/clawroom-lead/SKILL.md` — lead agent behavior
- `skills/clawroom/SKILL.md` — bridge agent prompt
