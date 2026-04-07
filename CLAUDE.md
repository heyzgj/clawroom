# CLAUDE.md — ClawRoom

## What this project is

ClawRoom is **bounded agent collaboration**. Two AI agents from different owners join a room with a goal and a list of required outcomes. They exchange messages, fill the outcomes, and close. Each owner gets a natural-language summary plus the structured result.

The product is two deployments and one skill:

| Component | Path | Lives at |
|---|---|---|
| Edge worker | `apps/edge/` | `https://api.clawroom.cc` |
| Landing + monitor | `apps/monitor/` | `https://clawroom.cc` |
| Canonical skill | `.agents/skills/clawroom/` | `https://clawroom.cc/skill.md` (synced) |

Everything else under `archive/` is the previous Python/FastAPI architecture and is no longer wired up. Do not edit it. Do not import from it.

## How to work on this project

### Role discipline

- Do NOT default to writing all the code yourself. Delegate to subagents (Codex, Explore, general-purpose) for implementation, search, and review.
- Your primary value is: planning, decomposition, verification, UX judgment, and strategic direction.
- Only write code directly when: (a) it's a small targeted fix, (b) UX/copy where judgment matters, (c) the subagent failed and you're recovering.

### Experimentation over building

- Real cross-runtime experiments find what actually breaks. Single-machine validation does not.
- Building features that look like Symphony / Paperclip / LangGraph is a waste — they have larger teams. Find what's different.
- Defining the right problem > building the right solution.

### Worker agent dispatch — known issues

- `isolation: "worktree"` agents cannot write files unless permissions are pre-granted.
- Background agents can't prompt for approval — they fail silently.
- "Spec-only" research agents (read codebase → produce plan) work great even without write access. Use them for any non-trivial design question.

## Stack

| Layer | Tech | Key files |
|---|---|---|
| Edge API | Cloudflare Workers + Durable Objects (TypeScript) | `apps/edge/src/worker*.ts` |
| Room state | SQLite-backed DO state machine | `worker_room.ts` |
| Registry / monitoring | Singleton DO | `worker_registry.ts` |
| Landing + monitor | Vanilla JS SPA (Vite) | `apps/monitor/` |
| Skill | SKILL.md + Python helper scripts | `.agents/skills/clawroom/` |

## Key conventions

- Durable Objects use `this.sql.exec()` with SQLite. Schema lives in `ensureSchema()`. Column migrations use `PRAGMA table_info` checks.
- Routing pattern: `worker.ts` matches the path → forwards to a DO via `stub.fetch()`.
- All responses go through `withCors(request, response)`.
- Monitor views are URL-param routed (`?ops=1`, `?room_id=…&host_token=…`).
- Monitor has no framework. Vanilla JS, CSS custom properties, JetBrains Mono + Inter.
- Skill scripts are designed for OpenClaw daemon-managed background exec. The poller is the only writer per room.

## What NOT to build next

- Generic task coordination infrastructure (mission registry, mission search) — that converges to Paperclip.
- Auth / trust systems — premature without users.
- Agent marketplace — premature without proving cross-owner execution at scale.
- Anything that isn't validated by a real experiment first.

## Competitive landscape

| System | What it does | Why we don't just use it |
|---|---|---|
| Google A2A | Protocol spec for agent interop | Spec only, no execution truth |
| Anthropic MCP | Agent → tool standardization | Not agent-to-agent |
| OpenAI Agents SDK | Single-owner orchestration | No cross-owner |
| Microsoft Agent Framework | Enterprise multi-agent | Single-org typical |
| Agent Relay | Slack-for-agents (free chat) | No structured outcomes |
| LangGraph | State-machine agent runtime | General, not room-shaped |

ClawRoom's actual differentiator: bounded cross-owner task rooms with structured outcomes, owner-in-the-loop, and real reliability instrumentation. Nothing else covers all four.

## Validated reliability (as of 2026-04-08)

| Metric | Value |
|---|---|
| S2 scenario suite | 9–10 / 10 |
| Avg room close time | 55–63s |
| Concurrent WS calls | 4 / 4 (was 0 / 6 with the old CLI path) |
| Cross-machine (local ↔ Railway) | Validated |
| Owner-in-the-loop (ASK_OWNER) | Validated |
| Messy-user scenarios | 9 / 10 (Test 7 mid-room context injection acceptable) |

Read [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) BEFORE adding any new feature. It contains every failure mode we hit, what we tried, and what worked. The catalog of LLM unreliability patterns (Sections A–G) is the single most useful document in the repo.

## Architectural rules learned the hard way

These are non-negotiable:

1. **Never trust the LLM with completion authority.** Every reliability win came from moving completion authority off the LLM and into code we wrote. Every reliability loss came from believing prompt rules would be followed.
2. **One writer per room.** Once the poller starts, only the poller writes to the room. Main session must never write.
3. **No `openclaw agent` CLI from background workers.** Concurrent CLI calls produce silent corruption (exit=0, garbage content). Always use the WebSocket client (`gateway_client.py`).
4. **Owner-actionable URLs are the right UX primitive.** Cancel, owner-reply, status — all served as signed URLs the owner can click. Bypasses the LLM entirely.
5. **Continuation hints are passive, not forced.** Server tells the poller "the room still needs work, here's what" via the `continuation` field on `/events`. The poller injects this into the LLM's NEXT prompt — it does NOT force extra LLM turns. Forced continuation creates new failure modes (we tried, it dropped us from 10/10 to 4/7).

## Repo layout quick reference

```
apps/edge/                       # Cloudflare Worker (api.clawroom.cc)
apps/monitor/                    # Vite landing + monitor (clawroom.cc)
.agents/skills/clawroom/         # Canonical skill
docs/LESSONS_LEARNED.md          # MUST read before making changes
docs/blog/                       # Public-facing technical writeups
archive/2026-04-08-pre-v2-cleanup/  # Old Python architecture, do not import
README.md                        # Public-facing project description
CLAUDE.md                        # This file
INSTALL_SKILL.md                 # Skill install instructions
```

## Operating tip

When in doubt about the current state of the project, run: `git log --oneline -10` and read `docs/LESSONS_LEARNED.md`. The lessons doc is updated alongside any architectural change and is the single source of truth for "what works and why."
