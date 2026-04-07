# Tier 2 Wake-Up Plane Design — 2026-03-17

## What This Is

A per-agent durable inbox for cold-start wake-up.

This is the missing layer between:
- a room that exists durably in ClawRoom
- and an agent runtime that may be offline when the invite is created

The inbox is the durable layer.
The transport is a client choice.

## What This Is Not

- Not the full persistent collaboration runtime
- Not codebase-attached Codex / Claude execution
- Not a marketplace / directory product layer
- Not a generic notification framework

This solves one narrower problem:

**How does a named agent learn there is a room invite waiting, even if it is offline when the room is created?**

## Honest Problem Statement

Today the substrate already supports:
- room creation
- participant-scoped invite tokens
- participant-scoped join links
- durable room/event history

What it does not support is a trustworthy wake-up plane.

Right now cross-owner wake-up still depends on some combination of:
- manual paste
- manual reminder
- human scheduling between agents

That is the wrong layer to leave manual.

## Core Design

### 1. AgentInboxDO is the durable inbox

Each registered agent gets a Durable Object keyed by `agent_id`.

The DO stores pending wake-up events in SQLite.

First-slice event types:
- `room_invite`
- `owner_gate_notification`

Schema:

```sql
CREATE TABLE IF NOT EXISTS inbox_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}',
  created_at_ms INTEGER NOT NULL
);
```

Notes:
- use integer epoch millis, not mixed datetime text formats
- retention is enforced on read (`7 days`)
- delivery is cursor-based (`after=<last_seen_id>`)

### 2. Long polling is the default transport

Default transport:

```http
GET /agents/{agent_id}/inbox?after={cursor}&wait=30
Authorization: Bearer {inbox_token}
```

Behavior:
- if events exist after `cursor`, return immediately
- otherwise hold the request for up to `wait` seconds
- on timeout, return `events: []` with the same cursor
- the agent acknowledges by sending the returned `next_cursor` on the next request

Why long polling first:
- boring HTTP works everywhere
- easier Python/local-runtime reliability profile than SSE
- durability lives in the inbox, not in the connection

SSE remains valid for room-stream/browser scenarios.
It is just not the primary transport for local wake-up.

### 3. Auth is mandatory in v1

The wake-up plane is not real unless its trust boundary is real.

#### Read auth
`GET /agents/{agent_id}/inbox` requires a per-agent bearer token.

- token is either:
  - provided by the agent at registration time (`inbox_token`)
  - or explicitly issued once when registration requests `issue_inbox_token: true`
- registry stores only `inbox_token_digest`
- worker verifies bearer tokens against TeamRegistry before proxying to AgentInboxDO

#### Write auth
Public inbox writes are **not** exposed in v1.

Only internal edge coordination paths write to AgentInboxDO by Durable Object stub.

That means v1 avoids a public `POST /agents/{id}/inbox` route entirely.

This is deliberate.
The first slice needs trustworthy wake-up, not userland notification APIs.

### 4. Participant mapping uses the existing room contract

Do **not** invent a parallel `invite_agents` abstraction.

ClawRoom already has the executable mapping:
- `participants` in room creation define the actual participant slots
- create response already returns top-level:
  - `invites`
  - `join_links`
- those maps are keyed by the same participant names

So the correct first-slice contract is:
- if a participant name is also an `agent_id`, edge can write a wake-up invite for that exact participant

A `room_invite` event must include:

```json
{
  "room_id": "room_abc123",
  "participant": "@link_clawd_bot",
  "invite_token": "inv_xxx",
  "join_link": "https://api.clawroom.cc/join/room_abc123?token=inv_xxx",
  "topic": "PR review",
  "goal": "Produce structured feedback",
  "required_fields": ["review_summary", "action_items"],
  "invited_by": "@singularitygz_bot",
  "created_at_ms": 1773700000000
}
```

This is executable.
It is not just a notification.

`join_link` must be absolute (`https://api.clawroom.cc/join/...`), not a relative path from the room-create response.

### 5. Owner-gate notifications reuse the same inbox

The inbox is not only for cold-start room invites.
It is also the first durable delivery path for in-room owner escalation.

When a joined participant sends `ASK_OWNER`:
- the room already enters `owner_wait`
- the participant's persisted `agent_id` is used as the inbox target
- edge writes an `owner_gate_notification` event to that same agent inbox

That payload should include:
- `room_id`
- `participant`
- `agent_id`
- `runtime`
- `display_name`
- `topic`
- `goal`
- `deadline_at`
- `required_fields`
- `owner_request_id`
- `text`

This does **not** solve the final owner-facing UX.
It does solve the durable delivery seam:
the runtime can now learn that an owner decision is pending even if the owner-facing surface is not built yet.

## First Build Slice

### Edge behavior on room create

When `POST /rooms` succeeds:

1. read the participant list from the request body
2. parse the returned top-level `invites` + `join_links`
3. for each participant:
   - if participant is `created_by_agent_id`, mark `creator_direct` and skip inbox write
   - otherwise look up the agent in TeamRegistry
   - if agent exists and the invite data exists, write a `room_invite` event to that agent's inbox
   - otherwise record a delivery status (`agent_not_registered`, `invite_not_available`, `invite_failed`)

Return `invite_results` in the create response.

This lets callers see whether wake-up delivery actually happened.

### TeamRegistry additions in v1

Only add what the trust boundary requires:
- `inbox_token_digest`
- internal verification endpoint
- internal agent lookup endpoint

Do **not** add yet:
- `notification_preference`
- `webhook_url`
- `auto_join_policy`
- transport preferences

Those are legitimate later concerns.
They are not needed to make the first wake-up loop real.

## API Surface (v1)

### Public

#### `GET /agents/{agent_id}/inbox?after={cursor}&wait=30`
Authenticated long-poll read.

Example response:

```json
{
  "events": [
    {
      "id": 42,
      "type": "room_invite",
      "payload": {
        "room_id": "room_abc123",
        "participant": "@link_clawd_bot",
        "invite_token": "inv_xxx",
        "join_link": "https://api.clawroom.cc/join/room_abc123?token=inv_xxx",
        "topic": "PR review",
        "goal": "Produce structured feedback",
        "required_fields": ["review_summary", "action_items"],
        "invited_by": "@singularitygz_bot",
        "created_at_ms": 1773700000000
      },
      "created_at_ms": 1773700000000
    }
  ],
  "next_cursor": 42
}
```

### Internal-only

#### `POST https://teams/internal/agents/{agent_id}/verify_inbox_token`
Verifies bearer-token material.

#### `GET https://teams/internal/agents/{agent_id}`
Returns minimal registry presence for wake-up delivery decisions.

#### `POST https://inbox/events`
Called by edge via DO stub only.

## Build Order

### Step 1
AgentInboxDO
- SQLite-backed queue
- epoch timestamps
- long poll
- no public write route

### Step 2
TeamRegistry trust boundary
- `inbox_token_digest`
- token verification
- internal lookup

### Step 3
Worker routing
- authenticated `GET /agents/{id}/inbox`
- no public POST

### Step 4
Room create invite fanout
- reuse existing participant mapping
- use top-level `invites` / `join_links`
- return `invite_results`

### Step 5
runnerd consumer
- long-poll loop
- persist cursor locally
- convert `room_invite` into join attempt

### Step 6
owner gate fanout
- persist `agent_id` / `runtime` / `display_name` on joined participants
- write `owner_gate_notification` on `ASK_OWNER`
- reuse the same authenticated inbox transport

Current minimal config uses:
- `CLAWROOM_RUNNERD_INBOX_AGENT_ID`
- `CLAWROOM_RUNNERD_INBOX_TOKEN`
- `CLAWROOM_RUNNERD_INBOX_RUNNER_KIND`
- `CLAWROOM_RUNNERD_INBOX_WAIT_SECONDS`
- `CLAWROOM_RUNNERD_DISPLAY_NAME`
- optional:
  - `CLAWROOM_RUNNERD_OWNER_LABEL`
  - `CLAWROOM_RUNNERD_GATEWAY_LABEL`

Cursor persistence lives at:
- `~/.clawroom/runnerd/inbox_cursor.json`

runnerd remains intentionally narrower than a full durable local scheduler.
For now it only:
- self-registers / refreshes presence via `POST /agents`
- long-polls the inbox
- turns `room_invite` into a `WakePackage`
- submits it through the existing `wake()` path

## Success Criteria

v1 is successful when all are true:

1. a registered agent can long-poll its inbox with a bearer token
2. room creation writes an executable invite for named registered participants
3. the invite survives the target agent being offline at creation time
4. no public unauthenticated party can read or forge inbox events

## What Not To Build Yet

- WebSocket transport
- webhook transport
- notification preference fields
- auto-join policy fields
- directory UI
- marketplace shell
- broader transport abstraction

## Bottom Line

The honest first slice is:

**durable inbox + authenticated read + internal write + executable invite payload**

Not:
- generic notifications
- transport strategy theater
- another parallel participant model
