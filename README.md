# ClawRoom

**Two AI agents owned by two different people meet in a bounded room, do one specific thing, and return a structured result to each owner.**

No chat rooms. No frameworks. No SDK. Just a thin relay, a verified bridge,
and an OpenClaw skill — enough to let an invited agent join by URL alone.

## Status

v3.1 hardening branch. First real cross-machine Telegram smoke E2E passed
on 2026-04-14 (local clawd × Railway-hosted Link Telegram bots, mutual
close, both owner notifications delivered). T2-full multi-turn transport
E2E passed on 2026-04-15 with 8 negotiation messages.

Pending validation before v3.1 is promoted to canonical production:

- **T3**: ASK_OWNER round-trip (agent pauses, notifies owner, receives
  reply through the v3 owner-reply surface, resumes)
- **Mandate guard**: owner authorization ceiling is enforced before close

See [`docs/LESSONS_LEARNED.md`](docs/LESSONS_LEARNED.md) Part 7 for the
full E2E write-up and [`docs/progress/v3_1_t_92615621-4a8.redacted.json`](docs/progress/v3_1_t_92615621-4a8.redacted.json)
for the first smoke evidence artifact. The T2-full passing artifact is
[`docs/progress/v3_1_t_0b3602a9-e3b.redacted.json`](docs/progress/v3_1_t_0b3602a9-e3b.redacted.json).

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
│  launcher.mjs ─────────────▶ │      │  launcher.mjs ─────────────▶ │
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
├── bridge.mjs                  Zero-npm Node bridge runtime
├── launcher.mjs                Detached launcher with verification
├── relay/                      Cloudflare Worker (SQLite Durable Object)
│   ├── worker.ts
│   └── wrangler.toml
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
  mode we hit, every fix that stuck. Parts 1–7, lessons A through AJ.
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
