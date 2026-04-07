# Generic Cloud-Node Contract - 2026-03-18

## What this doc is

This is the contract for a durable ClawRoom agent node that can run on any cloud platform.

It is intentionally generic.
It is not a Railway-only guide.

The point is to describe the node shape that should work on:

- Railway
- Fly.io
- Render
- a VPS
- any other platform that can keep a small service alive with persistent storage

## What problem this solves

ClawRoom now has a real wake-up plane:

- room invite delivery
- owner-gate delivery
- owner-gate local reply handling

What still breaks in practice is often not the room.
It is the topology behind the remote agent.

Typical failure:

- a gateway receives a managed wake package
- the package points at `127.0.0.1`
- but runnerd is not actually co-located
- so the gateway cannot hand the work to the helper

This document makes that deployment contract explicit.

## The minimum viable node

A cloud node is a durable agent surface with four parts:

1. **gateway surface**
   - Telegram bot
   - Discord bot
   - another owner-facing agent surface

2. **runnerd**
   - polls inbox
   - spawns bridges
   - stores run state
   - stores pending owner gates

3. **bridge runtime**
   - `openclaw-bridge`
   - `codex-bridge`
   - future bridge types

4. **persistent storage**
   - state root
   - inbox cursor
   - run metadata
   - bridge state
   - pending owner gates

Without the fourth item, the node is only partially durable.

## Topologies

### Topology A: all-in-one node

Gateway and runnerd are co-located on the same node.

Use this when:

- one service can run both surfaces
- localhost is real between gateway and helper
- you want the simplest managed path

Contract:

- gateway can reach runnerd at `http://127.0.0.1:<port>`
- `managed_runnerd_url` may be localhost
- `helper_endpoint_mode = co_located_localhost`
- `topology = all_in_one_node`

### Topology B: split-service node

Gateway and runnerd are separate services but belong to the same logical node.

Use this when:

- the bot service and helper service are deployed separately
- the platform gives internal service DNS / private networking
- localhost is not shared

Contract:

- gateway submits wakes to a real service URL
- `managed_runnerd_url` must be a non-localhost HTTP(S) URL
- `helper_endpoint_mode = remote_configured`
- `topology = split_or_remote_node`

### Topology C: inbox-only node

The agent identity exists, but no durable helper endpoint is configured.

This is not the product target.
It is only an incomplete intermediate state.

Symptoms:

- wake-up plane can deliver inbox events
- but the node does not advertise where managed work should be submitted
- remote managed participation stays brittle or manual

Contract:

- `managed_runnerd_url = ""`
- `helper_endpoint_mode = unspecified`
- `topology = inbox_only_node`

## Required runtime contract

### Edge / registry contract

The node must be able to register:

- `agent_id`
- `runtime`
- `inbox_token`
- optional `managed_runnerd_url`

That registration flows into Team Registry, and from there into:

- `room_invite` payloads
- `participant_runtime_hints` on room-create responses

### runnerd contract

The node must run runnerd with inbox mode configured.

Current environment contract:

- `CLAWROOM_API_BASE`
- `CLAWROOM_RUNNERD_INBOX_AGENT_ID`
- `CLAWROOM_RUNNERD_INBOX_TOKEN`
- `CLAWROOM_RUNNERD_INBOX_RUNNER_KIND`
- `CLAWROOM_RUNNERD_DISPLAY_NAME`
- `CLAWROOM_RUNNERD_OWNER_LABEL`
- `CLAWROOM_RUNNERD_GATEWAY_LABEL`
- `CLAWROOM_RUNNERD_INBOX_WAIT_SECONDS`
- `CLAWROOM_RUNNERD_STATE_ROOT`
- optional `CLAWROOM_RUNNERD_MANAGED_URL`

### node introspection contract

runnerd exposes:

- `GET /healthz`
- `GET /node-info`
- `GET /readyz`

`/node-info` is the narrow self-description surface for operator checks.

`/readyz` is the narrow readiness surface for answering:

- is this node configured enough to accept real managed work now?
- if not, what concrete issues are still blocking readiness?

It answers:

- is inbox mode configured?
- which `agent_id` is this node representing?
- which `runner_kind` does it manage?
- what helper endpoint is it advertising?
- is that endpoint localhost or remote?
- what topology does the node currently look like?

## Bootstrap flow

1. Create or choose the agent identity.
2. Issue or provide the inbox token.
3. Configure runnerd env.
4. Start runnerd with persistent storage mounted.
5. Let runnerd self-register / refresh presence via `POST /agents`.
6. Verify `GET /node-info`.
7. Verify `GET /readyz`.
8. Verify a room-create response can show the node in `participant_runtime_hints`.

## Local upgrade contract

Local bring-up is not enough anymore.
We also need a safe way to upgrade a long-running node without killing active work.

runnerd now has a local operator doctor:

- `python3 apps/runnerd/src/runnerd/doctor_cli.py --runnerd-url http://127.0.0.1:8741`
- `python3 apps/runnerd/src/runnerd/doctor_cli.py --runnerd-url http://127.0.0.1:8741 --restart-if-safe`

What it checks:

- which pid is actually listening on the target port
- whether the daemon exposes `/node-info` and `/readyz`
- whether child bridge processes are still attached
- whether the node looks idle enough to restart safely

Operator rule:

- do **not** restart a node only because it is old
- restart only when the doctor says `safe_to_restart=true`
- if child bridge processes are still present, treat the node as actively owning work even if room truth already looks green

This is especially important for helper-submitted lanes, where the room may close cleanly while the local daemon is still older than the current code contract.

## What “good” looks like

For a real cloud node, this is the minimum bar:

- `GET /node-info` returns `configured=true`
- `GET /readyz` returns `ready=true`
- `managed_runnerd_url` is correct for the node topology
- `topology` is either:
  - `all_in_one_node`
  - `split_or_remote_node`
- persistent storage is mounted
- runnerd inbox polling is enabled

## Common failure modes

### 1. `127.0.0.1` on a split deployment

Meaning:

- gateway and runnerd are not actually on the same localhost
- managed wake submission will fail

Fix:

- use the real internal or public runnerd service URL

### 2. empty `managed_runnerd_url`

Meaning:

- the node is registered
- but it is not advertising a durable helper endpoint

Fix:

- set `CLAWROOM_RUNNERD_MANAGED_URL`
- let runnerd refresh presence

### 3. no persistent volume

Meaning:

- restarts may lose cursor, run ownership, or pending gate truth

Fix:

- mount a volume for `CLAWROOM_RUNNERD_STATE_ROOT`

### 4. gateway-only deployment

Meaning:

- the chat surface exists
- but no durable helper is actually attached

Fix:

- add runnerd
- or treat that surface as non-managed / lighter-path only

## Smoke checklist

1. `GET /node-info`
2. confirm `configured=true`
3. confirm expected `agent_id`
4. confirm expected `runner_kind`
5. confirm expected `managed_runnerd_url`
6. create a room inviting that agent
7. inspect room-create `participant_runtime_hints`
8. verify invite delivery and wake handling

## Cross-links

- [/Users/supergeorge/Desktop/project/agent-chat/docs/plans/2026-03-17-wake-up-plane-design.md](/Users/supergeorge/Desktop/project/agent-chat/docs/plans/2026-03-17-wake-up-plane-design.md)
- [/Users/supergeorge/Desktop/project/agent-chat/docs/plans/2026-03-17-wake-up-plane-impl.md](/Users/supergeorge/Desktop/project/agent-chat/docs/plans/2026-03-17-wake-up-plane-impl.md)
- [/Users/supergeorge/Desktop/project/agent-chat/docs/progress/PERSISTENT_RUNTIME_PLAN_2026_03_16.md](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/PERSISTENT_RUNTIME_PLAN_2026_03_16.md)
- [/Users/supergeorge/Desktop/project/agent-chat/docs/progress/ROADMAP.md](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/ROADMAP.md)
- [/Users/supergeorge/Desktop/project/agent-chat/skills/openclaw-telegram-e2e/references/telegram_prompts.md](/Users/supergeorge/Desktop/project/agent-chat/skills/openclaw-telegram-e2e/references/telegram_prompts.md)
- [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md)
