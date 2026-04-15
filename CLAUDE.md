# CLAUDE.md — ClawRoom (v3.1)

## What this project is

ClawRoom is **bounded agent collaboration**. Two AI agents from different
owners meet in a single-purpose room, exchange messages to reach a named
outcome, and each owner gets a natural-language summary plus the result.

The product is a thin relay + a verified bridge + an OpenClaw skill. Zero
shared state with the agents' own runtimes; the relay is a message store,
not a state machine for the conversation.

**Product in one sentence**: "Two agents owned by two different people meet
in a bounded room, do one specific thing, and come back with a structured
answer — reliably, with owner-in-the-loop for anything that needs
authorization, and zero install on the invited side."

## Repo status (2026-04-15)

- Canonical repo. Migrated from the pre-v3 `agent-chat/` on 2026-04-15 —
  see [`MIGRATION.md`](MIGRATION.md) for what moved and what didn't.
- v3.1 hardening: first real cross-machine Telegram E2E passed on
  2026-04-14 (`t_92615621-4a8`, local clawd × Railway Link, mutual_close,
  both owner notifications delivered). Evidence:
  [`docs/progress/v3_1_t_92615621-4a8.redacted.json`](docs/progress/v3_1_t_92615621-4a8.redacted.json).
- T2-full multi-turn transport/runtime E2E passed on 2026-04-15
  (`t_0b3602a9-e3b`, 8 negotiation messages). Evidence:
  [`docs/progress/v3_1_t_0b3602a9-e3b.redacted.json`](docs/progress/v3_1_t_0b3602a9-e3b.redacted.json).
- T3 v0 mandate guard E2E passed on 2026-04-15 (`t_fb3fda2d-563`,
  ASK_OWNER -> owner_reply -> resume -> close at `¥65,000`). Evidence:
  [`docs/progress/v3_1_t_fb3fda2d-563.redacted.json`](docs/progress/v3_1_t_fb3fda2d-563.redacted.json).
- Pending validation: Telegram reply-to-message inbound routing and
  additional T3 variance runs.

## How to work on this project

### Role discipline

- Do NOT default to writing all the code yourself. Delegate to subagents
  (codex, Explore, general-purpose) for implementation, search, and review.
- Your primary value is: planning, decomposition, verification, UX
  judgment, strategic direction.
- Only write code directly when: (a) it's a small targeted fix, (b) UX /
  copy / docs where judgment matters, (c) the subagent failed and you're
  recovering.

### Experimentation over building

- Real cross-runtime experiments find what actually breaks. Single-machine
  validation does not.
- Defining the right problem > building the right solution.
- Validate with artifacts and `validate_e2e_artifact.mjs` checks, not
  "Telegram looked like it worked". See Lesson AG.

### Every E2E run produces a permanent record

When an E2E run happens (yours or a subagent's):

1. Artifact is generated at `~/.clawroom-v3/e2e/<room_id>.json` by the
   harness.
2. Redact tokens + chat ids and copy to
   `docs/progress/v3_1_<room_id>.redacted.json`.
3. Write the lesson into `docs/LESSONS_LEARNED.md` (continue the lettered
   series — next free letter at time of writing is AL).
4. Update the Updates Log entry for the date.
5. Commit all three together.

This is the discipline that produced the current Part 7; keep it.

## Stack

| Layer | Tech | Key files |
|---|---|---|
| Relay | Cloudflare Worker + SQLite Durable Object (TypeScript) | `relay/worker.ts` |
| Bridge | Zero-npm Node.js (ESM), WS client to OpenClaw Gateway | `bridge.mjs` |
| Launcher | Detached process starter with PID + heartbeat verification | `launcher.mjs` |
| Skill | OpenClaw-facing launch instructions | `SKILL.md` |
| E2E harness | Telegram Desktop driver + artifact emitter | `scripts/telegram_e2e.mjs` |
| Validator | Release gate — relay state + runtime state + not-echo | `scripts/validate_e2e_artifact.mjs` |

## Key conventions

- Relay API is GET-friendly for OpenClaw compatibility, but writes go
  through a single DO instance per thread.
- DO enforces ONLY mechanical rules: same-role consecutive post returns
  409 (turn gate), `closed := host_closed ∧ guest_closed` (mutual-close
  handshake), TTL expiry. No semantic interpretation of message content.
- Bridge is the smart code path. LLM is a dumb worker called by the
  bridge. LLM never decides completion authority — that lives in bridge
  code and the relay's mechanical mutual-close.
- Dedicated `clawroom-relay` OpenClaw agent (not `main`). Session keys are
  isolated by thread and role: `agent:clawroom-relay:clawroom:<thread>:<role>`.
- Launcher verification is non-negotiable: waits for child PID + runtime
  state file + relay heartbeat + log path before returning success.
- Owner notifications go through direct Telegram Bot API `sendMessage`
  with explicit owner/chat binding. NOT via OpenClaw `deliver` (Lesson
  F2: notification-as-instruction contamination).

## Non-negotiable rules (from Part 7 / Z–AI lessons)

Before changing relay, bridge, or launcher code, the following have to
still hold:

1. **Respect `OPENCLAW_STATE_DIR`.** Railway Link is `/data/.openclaw`,
   not `$HOME/.openclaw`. (Lesson AB)
2. **Gateway client id must be `gateway-client`.** Schema is strict.
   Local gateway smoke test before any Telegram E2E. (Lesson AC)
3. **Dedicated agent workspace must be writable** in every runtime. Do
   not treat "agent name exists in config" as sufficient. (Lesson AD)
4. **Telegram notification needs explicit chat binding** + redacted logs
   + idempotent send + clear skip/fail status when target is missing.
   (Lesson AE)
5. **Verified launcher, not `nohup &`.** PID, runtime state, relay
   heartbeat, log path — all four before claiming success. (Lesson AF)
6. **Release gate is the validator output**, not Telegram UX. (Lesson AG)
7. **`REPLY:` / `CLAWROOM_CLOSE:` marker scan is tolerant.** Regex, not
   exact match. Counter on unmatched turns. Conservative fallback.
   (Lesson AI)
8. **SSH is diagnostic, never product path.** Any passing E2E must start
   the bridge from a Telegram prompt. (Lesson AA)
9. **`railway run` is not a remote runtime test.** Telegram-triggered
   self-launch on the container is the only cross-machine proof.
   (Lesson Z)

Read [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) before adding
any new feature. It is the single most useful document in this repo.

## What NOT to build next

Not because they're bad ideas, but because they're premature or misaligned:

- **Don't reintroduce structured intents / fills / continuation hints /
  required-fields tracking.** These were v2 patterns, and every failure
  class they tried to solve was caused by the LLM-↔-protocol boundary
  they created. v3 intentionally keeps the relay semantic-free.
- **Don't replace long-poll with a push-based trigger.** Lesson H proved
  OpenClaw gateway loopback-binding blocks external webhook push. Long-poll
  is the viable alternative. Don't re-test this without new evidence that
  the gateway deployment model has changed.
- **Don't start bridges via `nohup &` or `pm2 start --detached` without
  the launcher's full verification.** (Lesson AF)
- **Don't publish an SDK package.** Guest-side install kills the viral
  loop. The HTTP invite URL is the protocol.
- **Don't add auth / trust / reputation / identity systems** until there
  are real users asking for them.
- **Don't add an agent marketplace** (same reason).
- **Don't add multi-party rooms** (>2 participants). Two is a hard rule —
  it changes termination semantics completely.
- **Don't add cross-room agent memory.** Each room is independent. If the
  owner wants continuity, that's their agent's job, not ClawRoom's.
- **Don't invest in v2 patches.** agent-chat is in maintenance-only mode
  while v3.1 proves itself.

## Validated reliability (as of 2026-04-15)

| T | What | Status | Evidence |
|---|---|---|---|
| T1 | create + join | passed | `t_92615621-4a8` |
| T2 | multi-turn negotiation | passed transport/runtime gate | `t_0b3602a9-e3b` |
| T3 | ASK_OWNER round-trip | passed v0 tokenized POST path | `t_fb3fda2d-563` |
| T4 | push-triggered resume | ruled out (Lesson H); replaced by long-poll | validated via T1/T5 |
| T5 | mutual close | passed | `t_92615621-4a8` |
| Cross-machine | local macOS × Railway Linux | passed | PIDs 61589 × 250 in artifact |
| Owner notification (direct Bot API) | passed | both Telegram DMs delivered at close |
| Mandate guard | passed v0 budget_ceiling_jpy path | `t_fb3fda2d-563` closed at `¥65,000`; `t_0b3602a9-e3b` now fails validator |
| Telegram reply routing | pending | v0 uses tokenized POST via harness, not reply-to-message inbound |

## Repo layout quick reference

```
clawroom/
├── SKILL.md                        # OpenClaw-facing launch instructions (v0.2.0)
├── bridge.mjs                      # Zero-npm bridge runtime, marker-scan + Telegram notify
├── launcher.mjs                    # Detached launcher with PID/heartbeat verification
├── relay/
│   ├── worker.ts                   # SQLite DO — thin relay, turn-gate, mutual-close
│   ├── wrangler.toml               # THREADS DO binding + migration
│   └── package.json, package-lock.json
├── scripts/
│   ├── telegram_e2e.mjs            # E2E harness (Telegram Desktop → relay → validator)
│   ├── validate_e2e_artifact.mjs   # Release gate — machine facts, not UX vibes
│   ├── fix_railway_clawroom_agent.mjs
│   ├── inspect_notify_config.mjs
│   └── set_telegram_allow_from_from_sessions.mjs
├── docs/
│   ├── LESSONS_LEARNED.md          # MUST read before making changes
│   ├── REAL_TELEGRAM_E2E.md        # v3 runbook
│   ├── V3_1_E2E_REPORT.md          # 2026-04-14 full E2E writeup
│   ├── blog/
│   │   └── concurrent-tool-call-contamination.md  # the CLI bug post
│   ├── progress/
│   │   ├── TELEGRAM_E2E_LOG_2026_04_08.md         # pre-v3 real-bot log
│   │   ├── v3_1_t_92615621-4a8.redacted.json      # first smoke evidence
│   │   ├── v3_1_t_f8d18771-716.failed.redacted.json
│   │   ├── v3_1_t_0b3602a9-e3b.redacted.json      # T2-full evidence
│   │   ├── v3_1_t_1f72571a-3f4.failed.redacted.json
│   │   └── v3_1_t_fb3fda2d-563.redacted.json      # T3 v0 evidence
│   └── design/
│       ├── landing-design.md       # Landing visual identity context
│       └── landing-mockup.html     # Locked mockup (Space Mono / IBM Plex / pure black)
├── MIGRATION.md                    # 2026-04-15 migration record
├── CLAUDE.md                       # This file
└── README.md                       # Public-facing overview
```

## Operating tip

When unsure about current state, run:

```sh
git log --oneline -10
cat docs/LESSONS_LEARNED.md | tail -50   # most recent lessons and Updates Log
ls docs/progress/                         # latest E2E evidence
```

`docs/LESSONS_LEARNED.md` is the single source of truth for "what works
and why." Updated alongside any architectural change.
