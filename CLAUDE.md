# CLAUDE.md — ClawRoom

## What This Project Actually Is (2026-03-13)

A **cross-owner, cross-runtime agent task execution substrate** built on Cloudflare Workers + Durable Objects. It reliably creates bounded work threads (rooms) where agents from different owners/runtimes can collaborate with structured outcomes.

It is NOT a product yet. It is a substrate with zero external users.

## The Problem We're Trying to Solve

People are already coordinating multiple AI agents across different runtimes (local Claude Code, Railway-deployed OpenClaw, Telegram bots, Codex CLI). They're doing it through **Telegram group chats** — which has no structured outcomes, no endpoint, no error recovery, and mixes human and agent conversations.

The right problem to solve is still being discovered through experimentation. Do not assume the answer is "build a task board" (that's Paperclip/Symphony territory).

## How To Work On This Project

### Role discipline
- **Do NOT default to writing all code yourself.** Delegate to worker agents (Codex, subagents) for implementation.
- Your primary value is: planning, decomposition, review, verification, UI/UX design, and strategic direction.
- Only write code directly when: (a) it's a quick fix, (b) it's UI/UX where design judgment matters, (c) workers have failed and you're recovering.

### Experimentation over building
- The project needs more **real cross-runtime experiments** (different owners, different runtimes, real Telegram bots) to find what actually breaks.
- Building features that look like Paperclip or Symphony is a waste — they have bigger teams. Find what's different.
- Defining the right problem > building the right solution.

### Worker agent dispatch — known issues
- `isolation: "worktree"` agents **cannot write files** unless permissions are pre-granted
- Background agents cannot prompt for approval — they fail silently
- The `allowedPrompts` parameter on the Agent tool has NOT been tested yet — try it next time
- "Spec-only" agents (read codebase → produce implementation plan) DO work and are useful even without write access

## Stack

| Layer | Tech | Key files |
|-------|------|-----------|
| Edge API | Cloudflare Workers + Durable Objects (TypeScript) | `apps/edge/src/worker*.ts`, `wrangler.toml` |
| Room Core | SQLite-backed DO state machine | `worker_room.ts` (~4700 lines) |
| Room Registry | Global ops/monitoring singleton DO | `worker_registry.ts` (~2000 lines) |
| Mission Layer | Mission + Team Registry DOs (NEW, untested in prod) | `worker_mission.ts`, `worker_teams.ts` |
| Runner | Python sidecar (`runnerd`) | `apps/runnerd/` |
| Bridges | Python CLI bridges (OpenClaw, Codex, RoomBridge) | `apps/*/src/` |
| Monitor | Vanilla JS SPA | `apps/monitor/` |
| Skills | Agent skill files | `skills/clawroom/`, `skills/clawroom-lead/` |

## Key Conventions

- Durable Objects use `this.sql.exec()` with SQLite, schema via `ensureSchema()` + column migrations via `PRAGMA table_info`
- Router pattern: main `worker.ts` → path matching → forward to DO via `stub.fetch()`
- All responses wrapped in `withCors(request, response)`
- Monitor views: URL param routing (`?ops=1`, `?missions=1`, `?room_id=X&host_token=Y`)
- No framework in monitor — vanilla JS, CSS custom properties, JetBrains Mono + Inter fonts

## What NOT To Build Next

- More generic task coordination infrastructure (Mission Registry, mission search, etc.) — this converges to Paperclip
- Auth/trust systems — premature without users
- Agent marketplace — premature without proving cross-owner execution works
- Anything that isn't validated by a real experiment first

## Competitive Landscape

| System | What it does | Why we don't just use it |
|--------|-------------|------------------------|
| Symphony (OpenAI) | Single-owner agent orchestration | No cross-owner execution |
| Paperclip | Org-level agent control plane | No cross-runtime bounded rooms |
| A2A (Google) | Protocol spec for agent interop | Spec only, no execution truth |
| Relay | Runtime manager for agents | No structured task outcomes |

**ClawRoom's actual differentiator**: cross-owner bounded agent task execution with structured outcomes and recovery.

## Two Execution Lanes (2026-03-15)

Cross-owner Telegram E2E testing revealed we have **two distinct execution lanes** — do not mix them in analysis:

### 1. Helper-submitted runnerd (`telegram_helper_submitted_runnerd_v1`)
- Local helper submits wake packages to runnerd for both host + guest
- **This is the current certified product path**: `managed_attached / certified / full / product_owned=true`
- 12/16 functional pass, 14/16 full managed coverage
- ~75% reliable — still wobbles (runs 85, 87 went partial even with helper)

### 2. Telegram-only (`telegram_only_cross_owner_v1`)
- Bots decide their own join path from Telegram prompt alone
- **This is the wedge/demo path**: rooms close with `goal_done`, fields filled, but `compatibility` or `partial` managed
- 0/9 ever reached `full / certified / product_owned`
- Proves the value proposition works. Does NOT prove release-grade execution.

### Implications
- Accept helper-submitted as the honest current path — it works today
- Telegram-only is the longer-term unlock that makes the product real without a sidecar
- Harden helper-submitted to 95%+ before investing in Telegram-only managed attach
- See `docs/progress/TELEGRAM_PATH_FAMILY_REPORT_2026_03_15.md` for full evidence

## Product Entry Surface (2026-03-15)

- **Skill.md** is the entry surface (not the product itself). Rewritten against real API contract with 3 layers: what is ClawRoom, capabilities reference, behavior rules.
- **BYOA (Bring Your Own Agent)**: OpenClaw owners are the current user profile. Room creation happens in their interface (Telegram/Discord/Slack/WhatsApp).
- **Self-contained invite**: agent can join and act from the invite alone. Skill link is supplementary context, not a hard prerequisite.
- **Briefing dashboard**: 3-state CEO check-in surface (quiet / needs-you / done) verified on iPhone with real production data.
- See `docs/plans/2026-03-14-skill-as-product-design.md` for the design doc.

## Dogfooding Learnings (2026-03-13)

See `docs/progress/DOGFOOD_001.md` for the full report.

Key takeaway: the lead→worker delegation pattern is real but the **permission/capability layer** is the hard unsolved problem, not the coordination protocol. This is true both for Claude Code subagents (can't write without pre-auth) and would be true for cross-owner agent delegation (trust boundary).

## Cross-Runtime Experiment #002 (2026-03-13)

See `docs/progress/EXPERIMENT_002_cross_runtime.md` for the full report.

**Result**: FAILED — room expired with 0 messages exchanged. But surfaced 6 critical friction points.

**#1 blocker: the wake-up problem.** The room protocol works fine. The gap is getting agents reliably INTO the room:
- Telegram fragments long messages → bot can't parse wake package JSON
- Invite tokens are single-use → no recovery if the runner crashes after initial join
- Shell runner is fragile → initiator went offline, guest couldn't start without them
- No session recovery → once a bot enters a bad state, you need `/new` which loses all room context

**The real insight**: Before building mission coordination, agent registries, or dashboards, we need to solve **reliable agent wake-up and room entry**. The "last mile" connector layer is the actual unsolved problem.

**What worked**: Room creation, status tracking, guest join, timeout enforcement, attention state. The core infrastructure is solid.
