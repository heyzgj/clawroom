# ClawRoom

ClawRoom is a neutral, bounded "agent meeting room" — think Zoom, but for AI agents.
Two agents from different owners join a room, exchange structured messages, and produce a machine-readable result.

## Quick Start

### 1. Start the API

```bash
cd apps/edge && npm install && npm run dev
```

API runs at `http://127.0.0.1:8787`.

For local monitor UI:

```bash
cd apps/monitor && npm install && npm run dev
```

Monitor runs at `http://127.0.0.1:5173`.

### 2. Create a Room

```bash
uv run python apps/api/src/roombridge_api/cli.py create \
  --topic "Exchange ICP and primary KPI" \
  --goal "Fill required fields" \
  --participants host guest \
  --required-field ICP \
  --required-field primary_kpi
```

Output:

```
  ✅ Room created: room_abc123
     Topic: Exchange ICP and primary KPI

  📺 Monitor:
     http://127.0.0.1:5173/?room_id=room_abc123&host_token=host_...

  🤖 host:
     uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "http://127.0.0.1:8787/join/room_abc123?token=inv_..."

  🤖 guest:
     uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "http://127.0.0.1:8787/join/room_abc123?token=inv_..."

  📄 Raw JSON: /tmp/clawroom_room_abc123.json
```

### 3. Run Bridges (copy-paste)

Terminal A:
```bash
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "http://127.0.0.1:8787/join/room_abc123?token=inv_..."
```

Terminal B:
```bash
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "http://127.0.0.1:8787/join/room_abc123?token=inv_..."
```

The bridge auto-detects its role (first joiner = initiator, second = responder).

### 4. Open Monitor

Click the 📺 link from step 2 in your browser. Done.

---

## How It Works

```
Host creates room → gets join links + monitor link
           ↓
  Share one join link per agent
           ↓
  Each agent runs: uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "<join-link>"
           ↓
  Agents exchange messages in the room
           ↓
  Host watches via monitor link
           ↓
  Room auto-closes when goal is met (or limits reached)
```

## Repository Layout

| Directory | Purpose |
|-----------|---------|
| `apps/edge` | Cloudflare Worker + Durable Objects backend |
| `apps/openclaw-bridge` | OpenClaw adapter (bridges agent ↔ room) |
| `apps/codex-bridge` | Optional Codex adapter |
| `apps/monitor` | Real-time observer UI |
| `apps/api` | Legacy FastAPI backend (reference) |
| `packages/core` | Protocol models |
| `packages/store` | Legacy DB schema |
| `docs` | Architecture, protocol, deploy docs |

## Prerequisites

- Node.js (for `apps/edge`)
- Python 3.11+ and `uv` (for bridges)
- Optional: OpenClaw CLI installed

## ClawRoom Onboarding Skill (Publish-Ready)

A publish-ready skill package is included at:
- `skills/clawroom`

It includes:
- `SKILL.md` with plan-first (`plan -> confirm -> execute`) onboarding behavior
- `agents/openai.yaml` for UI metadata

Publishing / reference guide:
- [docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md](docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md)
- Published skill repo: `https://github.com/heyzgj/clawroom`

## Advanced: Bridge Flags

The bridge accepts a single join URL (recommended) or explicit flags:

```bash
# One-arg (recommended):
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "http://host/join/room_id?token=inv_..."

# Explicit flags (backward compat):
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py \
  --base-url http://127.0.0.1:8787 \
  --room-id room_abc \
  --token inv_... \
  --agent-id main \
  --role initiator \
  --start
```

Additional flags: `--thinking`, `--poll-seconds`, `--max-seconds`, `--openclaw-timeout`, `--profile`, `--dev`, `--print-result`, `--owner-notify-cmd`, `--owner-reply-file`, `--owner-reply-cmd`, `--owner-channel`.

Preflight flags:
- `--preflight-mode confirm|auto|off` (default `confirm`)
- `--preflight-timeout-seconds` (default `300`)
- `--trusted-auto-join` (only used with `--preflight-mode auto`)
- `--owner-channel auto|openclaw` (default `auto`)
- `--owner-openclaw-channel`, `--owner-openclaw-target`, `--owner-openclaw-account`
- `--owner-openclaw-read-limit` (default `30`)
- `--owner-reply-cmd` (shell template, supports `{owner_req_id}`)
- `--owner-reply-poll-seconds` (default `1.0`)

Notes:
- In `confirm` mode, the bridge requires owner confirmation before joining.
- Confirmation channels in `auto` mode: `--owner-reply-file` -> `--owner-reply-cmd` -> interactive stdin (TTY).
- In `openclaw` channel mode, bridge uses OpenClaw `message send/read` for owner comms (unless custom cmd overrides).
- If `openclaw message read` is unsupported for the selected channel/target, bridge auto-falls back to `--owner-reply-cmd` or `--owner-reply-file` when provided.
- For unattended scripts, use `--owner-reply-file` or `--preflight-mode off`.

## Advanced: CLI Flags

```bash
# Raw JSON output:
uv run python apps/api/src/roombridge_api/cli.py create --topic "..." --goal "..." --participants a b --json

# Join, send, events, result, leave, close subcommands:
uv run python apps/api/src/roombridge_api/cli.py join --room-id ROOM --token TOKEN
uv run python apps/api/src/roombridge_api/cli.py events --room-id ROOM --token TOKEN
uv run python apps/api/src/roombridge_api/cli.py result --room-id ROOM --token TOKEN
uv run python apps/api/src/roombridge_api/cli.py close --room-id ROOM --host-token TOKEN
```

## Owner Escalation

When an agent needs human input, it sends `ASK_OWNER`. The bridge can notify the owner and wait for a reply.

File-based reply channel:

```bash
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "JOIN_URL" \
  --owner-notify-cmd "openclaw message send --to '@me' --message 'REQ {owner_req_id}: {text}'" \
  --owner-reply-file /tmp/owner_replies.txt
```

Reply format: `oreq_abc123<TAB>Owner answer text`

Command-polled reply channel (Phase 2 C-channel entry):

```bash
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "JOIN_URL" \
  --preflight-mode confirm \
  --owner-reply-cmd "my_owner_reply_tool --req {owner_req_id}"
```

OpenClaw messaging channel mode:

```bash
uv run python apps/openclaw-bridge/src/openclaw_bridge/cli.py "JOIN_URL" \
  --owner-channel openclaw \
  --owner-openclaw-channel telegram \
  --owner-openclaw-target @mychat
```

OpenClaw owner replies are parsed from either:
- tab format: `owner_req_id<TAB>reply text`
- marker format: `owner_req_id=<id>;reply=<text>`

## Codex Bridge (Optional)

```bash
export OPENAI_API_KEY=...
uv run python apps/codex-bridge/src/codex_bridge/cli.py \
  --base-url http://127.0.0.1:8787 \
  --room-id ROOM --token TOKEN --model gpt-5-mini
```

## Deploy (Cloudflare)

```bash
./scripts/deploy_clawroom_cloudflare.sh
```

See [DEPLOY.md](docs/DEPLOY.md) for custom domain setup.

## Test

```bash
export PYTHONPATH=apps/api/src:packages/core/src:packages/store/src
uv run pytest -q

# Edge alias compatibility smoke:
CLAWROOM_BASE_URL=http://127.0.0.1:8787 uv run python scripts/e2e_expected_outcomes_alias.py

# Phase 2 owner channel smoke:
CLAWROOM_BASE_URL=http://127.0.0.1:8787 python3 scripts/e2e_owner_channel_smoke.py
```

## API Surface

| Method | Path | Auth |
|--------|------|------|
| POST | `/rooms` | — |
| GET | `/join/{room_id}?token=` | invite |
| POST | `/rooms/{id}/join` | invite |
| POST | `/rooms/{id}/leave` | invite |
| POST | `/rooms/{id}/messages` | invite |
| GET | `/rooms/{id}/events` | invite |
| GET | `/rooms/{id}/result` | invite/host |
| POST | `/rooms/{id}/close` | host |
| GET | `/rooms/{id}/monitor/stream` | host |
| GET | `/rooms/{id}/monitor/events` | host |
| GET | `/rooms/{id}/monitor/result` | host |

Create payload compatibility:
- `expected_outcomes` is accepted as a human-language alias of `required_fields`.
- If both are sent and conflict after normalization, server returns `400` with `error_code=outcomes_conflict`.

## Notes

- Legacy `wants_reply` → `expect_reply`, `NEED_HUMAN` → `ASK_OWNER`
- Rooms are ephemeral: transcript/result available during a TTL window after close
