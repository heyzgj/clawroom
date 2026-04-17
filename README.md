# ClawRoom

**Two AI agents owned by two different people meet in a bounded room, do one specific thing, and return a structured result to each owner.**

No chat rooms. No frameworks. No SDK. Just a thin relay, a verified bridge,
and an OpenClaw skill — enough to let an invited agent join by URL alone.

## Status

v3.1 hardening branch. First real cross-machine Telegram smoke E2E passed
on 2026-04-14 (local clawd × Railway-hosted Link Telegram bots, mutual
close, both owner notifications delivered). T2-full multi-turn transport
E2E passed on 2026-04-15 with 8 negotiation messages. T3 v0 mandate
guard E2E passed on 2026-04-15 with ASK_OWNER, owner_reply, resume, and
close at the authorized ceiling. The 2026-04-17 stability matrix passed
three more cross-machine rooms: calendar coordination, product launch
communication, and term-sheet negotiation with real Telegram inbound
owner reply on the Railway Link side.

Pending work before v3.1 is promoted to canonical production:

- **Install path**: make first-time skill install pull only the product
  runtime files (`SKILL.md`, `clawroomctl.mjs`, `launcher.mjs`,
  `bridge.mjs`)
- **Product-path variance**: repeat natural Telegram create/join flows
  after the install path and relay capacity are fixed
- **Relay capacity**: the current public relay hit Cloudflare Durable
  Objects free-tier quota during wrapper smoke after stale local bridges
  from old tests kept polling invalid rooms; the bridge now exits on
  auth/not-found errors and backs off on quota/server errors, but use
  Workers Paid or a paid staging relay before inviting outside users
- **Hosted relay admission**: public installs should use BYO relay by default
  or a private-beta create key for the hosted relay

See [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) Part 7 for the
full E2E write-up and [`docs/progress/v3_1_t_92615621-4a8.redacted.json`](docs/progress/v3_1_t_92615621-4a8.redacted.json)
for the first smoke evidence artifact. The T2-full passing artifact is
[`docs/progress/v3_1_t_0b3602a9-e3b.redacted.json`](docs/progress/v3_1_t_0b3602a9-e3b.redacted.json).
The T3 v0 passing artifact is
[`docs/progress/v3_1_t_fb3fda2d-563.redacted.json`](docs/progress/v3_1_t_fb3fda2d-563.redacted.json).
The 2026-04-17 stability matrix artifacts are
[`docs/progress/v3_1_t_dba18332-f9f.avg-calendar.redacted.json`](docs/progress/v3_1_t_dba18332-f9f.avg-calendar.redacted.json),
[`docs/progress/v3_1_t_0babf6d2-297.product-launch.redacted.json`](docs/progress/v3_1_t_0babf6d2-297.product-launch.redacted.json),
and [`docs/progress/v3_1_t_10f2b0e8-b00.term-sheet-telegram-owner-reply.redacted.json`](docs/progress/v3_1_t_10f2b0e8-b00.term-sheet-telegram-owner-reply.redacted.json).

## Architecture

```
┌──────────────────────────────┐      ┌──────────────────────────────┐
│ Owner A                       │      │ Owner B                       │
│ (Telegram / Feishu / etc)     │      │ (Telegram / Feishu / etc)     │
└──────────────┬───────────────┘      └──────────────┬───────────────┘
               │ ① "please talk to                    │
               │    their agent"                      │
               ▼                                       ▼
┌──────────────────────────────┐      ┌──────────────────────────────┐
│ OpenClaw host (e.g. clawd)    │      │ OpenClaw guest (e.g. Link)   │
│ + clawroom skill              │      │ + clawroom skill              │
│                              │      │                              │
│  clawroomctl.mjs ──────────▶ │      │  clawroomctl.mjs ──────────▶ │
│   launcher.mjs ────────────▶ │      │   launcher.mjs ────────────▶ │
│   bridge.mjs (host role)     │      │   bridge.mjs (guest role)    │
│    - long-polls relay        │      │    - long-polls relay        │
│    - calls OpenClaw via WS   │      │    - calls OpenClaw via WS   │
│    - scans REPLY:/_CLOSE:    │      │    - scans REPLY:/_CLOSE:    │
│    - direct Telegram notify  │      │    - direct Telegram notify  │
└──────────────┬───────────────┘      └──────────────┬───────────────┘
               │                                       │
               └───────────────────┬───────────────────┘
                                   │ ② POST / GET long-poll
                                   ▼
                ┌──────────────────────────────────┐
                │ Cloudflare Worker Relay           │
                │ (SQLite Durable Object per thread)│
                │                                   │
                │ • thin mailbox (create, post,     │
                │   long-poll, close, heartbeat)    │
                │ • mechanical rules only:          │
                │   - 409 on same-role consecutive  │
                │   - closed := host ∧ guest closed │
                │   - TTL expiry                    │
                │ • no semantic interpretation      │
                │                                   │
                │ clawroom-v3-relay.heyzgj.workers.dev │
                └──────────────────────────────────┘
```

Everything smart lives in `bridge.mjs` — goal tracking, mandate checking
(what the owner authorized), summary extraction, marker-scan parsing of
OpenClaw output. The relay is deliberately semantic-free.

Everything owner-facing about launch lives in `clawroomctl.mjs`: it starts
the verified runtime but prints only a safe public message, while tokens,
PIDs, and log paths stay in local state.

## Try it (reproduce the passing E2E)

You need:

- Cloudflare account with Workers + Durable Objects enabled
- Two OpenClaw installations (local + remote) with Telegram bots
- `TG_BOT_TOKEN` or `TELEGRAM_BOT_TOKEN` in env, plus each bot's chat id

```sh
# 1. Deploy the relay
cd relay
npm install && npx wrangler deploy
cd ..

# 2. Ensure both OpenClaw runtimes have:
#    - Node 22.4+ with stable built-in WebSocket
#    - Writable workspace for the `clawroom-relay` dedicated agent
#    - OPENCLAW_STATE_DIR correctly set (Railway uses /data/.openclaw)
node -p "process.version + ' ' + typeof WebSocket"
node scripts/fix_railway_clawroom_agent.mjs   # optional helper

# 3. Drive an E2E through Telegram Desktop
node scripts/telegram_e2e.mjs

# 4. Validate the resulting artifact
node scripts/validate_e2e_artifact.mjs --artifact ~/.clawroom-v3/e2e/<room_id>.json
```

Full runbook: [`docs/REAL_TELEGRAM_E2E.md`](docs/REAL_TELEGRAM_E2E.md).

## Repo layout

```
.
├── SKILL.md                    OpenClaw-facing launch instructions
├── clawroomctl.mjs             Product-safe create/join wrapper
├── bridge.mjs                  Zero-npm Node bridge runtime
├── launcher.mjs                Detached launcher with verification
├── relay/                      Cloudflare Worker (SQLite Durable Object)
│   ├── worker.ts
│   └── wrangler.toml
├── skills/deploy-clawroom-relay/
│   └── SKILL.md                 Agent-friendly BYO relay deploy skill
├── scripts/                    E2E harness, validator, Railway helpers
├── docs/
│   ├── LESSONS_LEARNED.md      ← READ THIS BEFORE CHANGING ANYTHING
│   ├── REAL_TELEGRAM_E2E.md    v3 runbook
│   ├── V3_1_E2E_REPORT.md      2026-04-14 E2E writeup
│   ├── blog/                   Public-facing technical posts
│   ├── progress/               E2E logs and redacted artifacts
│   └── design/                 Landing page design context + mockup
├── MIGRATION.md                2026-04-15 migration record from agent-chat
├── CLAUDE.md                   Project guidance for Claude Code sessions
└── README.md                   (this file)
```

## Background reading

- [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) — every failure
  mode we hit, every fix that stuck. Parts 1–7, lessons A through AK.
  Non-optional reading before touching relay / bridge / launcher code.
- [`docs/blog/concurrent-tool-call-contamination.md`](docs/blog/concurrent-tool-call-contamination.md)
  — the silent CLI bug that took us weeks to find, and why the bridge
  uses the gateway WebSocket client instead.

## Why v3 (versus v2)

v2 (in `agent-chat/` — now archived) tried to make the server understand
the conversation: structured intents (`ANSWER` / `ASK_OWNER` / `DONE`),
required-field tracking, continuation hints, placeholder rejection, etc.
Every documented failure mode (A through G in LESSONS_LEARNED) came from
that LLM-↔-protocol boundary. The server required the LLM to produce
correctly-structured output; the LLM occasionally didn't.

v3 inverts it: **the server is a mechanical mailbox, and all semantic
judgment lives in the bridge** (code the owner controls). Every
reliability guardrail moved from "the server enforces it" to "the bridge
or the LLM's own deterministic wrapper enforces it". The result is a
relay that is about 10× smaller than the v2 worker, and a failure-mode
inventory that is understandable at the bridge layer where it can be
fixed without protocol migration.

## License

Not yet decided. Treat as "all rights reserved" until a LICENSE file lands.
