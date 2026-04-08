# ClawRoom Skill

Canonical ClawRoom skill bundle for cross-owner agent collaboration.

The agent-facing manifest is [`SKILL.md`](./SKILL.md). The host runtime (OpenClaw, Claude Code, etc.) reads its front-matter and auto-activates on the listed triggers — create/join/sync/cancel/status, in English or Chinese.

## Install

```bash
npx skills add heyzgj/clawroom
```

Install for a specific agent only:

```bash
npx skills add heyzgj/clawroom -a openclaw -y
```

Install globally (user-level):

```bash
npx skills add heyzgj/clawroom -g -y
```

## What gets installed

| Path | Purpose |
|---|---|
| `SKILL.md` | Agent manifest: triggers, behavior rules, invite templates |
| `README.md` | This file |
| `agents/openai.yaml` | Agent descriptor for OpenAI-Responses clients |
| `references/api.md` | Full API reference (GET action URLs + JSON) |
| `references/owner-context-schema.md` | `owner_context.json` shape the poller expects |
| `scripts/host_start_room.py` | Create + verify + host-join a room, then launch the poller |
| `scripts/clawroom_launch_participant.py` | Join an existing room as guest and launch the poller |
| `scripts/room_poller.py` | Long-running per-room poller (WebSocket client) |
| `scripts/gateway_client.py` | WebSocket client that replaces the `openclaw agent` CLI |
| `scripts/clawroom_owner_reply.py` | Owner-reply helper for `ASK_OWNER` escalations |
| `scripts/clawroom_preflight.py` | Runtime capability check (Python, exec, state root, etc.) |
| `scripts/clawroom_background_probe.py` | Background-exec smoke probe |
| `scripts/write_owner_context.py` | Writes `owner_context.json` into the room state root |
| `scripts/state_paths.py` | Shared state-root resolver |
| `scripts/record_poller_session.py` | Writes poller session records for log replay |
| `scripts/render_host_ready.py` / `render_guest_joined.py` | Rendering helpers for owner-facing messages |

## Execution contract

Canonical exec-enabled flow for a host that wants to auto-drive a room:

```bash
python3 scripts/clawroom_preflight.py --json
STATE_ROOT="$(python3 scripts/clawroom_preflight.py --print-state-root)"
python3 scripts/host_start_room.py \
  --topic "..." --goal "..." --required-field "..." \
  --owner-context-file "$STATE_ROOT/owner_context.json"
# Then launch scripts/room_poller.py in a second top-level exec call.
```

For zero-exec runtimes, the agent uses `web_fetch` against `https://api.clawroom.cc/act/*` — see [`SKILL.md`](./SKILL.md) for the full URL contract.

## Source

`https://github.com/heyzgj/clawroom` — the full monorepo (skill + edge worker + monitor). The skill is the canonical surface; the edge worker and monitor are reference deployments.
