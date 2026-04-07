# Wake-Up Plane Implementation Plan (v2)

## Goal

Ship the smallest trustworthy wake-up plane:
- per-agent durable inbox
- authenticated long-poll read
- room-create invite fanout using real participant slots
- no public write endpoint

This is the first slice only.
It is not the full persistent runtime.

## Task 1 — AgentInboxDO

**File**
- `apps/edge/src/worker_inbox.ts`

**Implement**
- Durable Object keyed by `agent_id`
- SQLite table `inbox_events`
- fields:
  - `id`
  - `type`
  - `payload_json`
  - `created_at_ms`
- `POST /events` for internal writes
- `GET /events?after={cursor}&wait=30` for long-poll reads
- prune by integer cutoff (`7 days`)

**Contract**
- supported event types:
  - `room_invite`
  - `owner_gate_notification`
- payload must be an object
- max wait = `30s`
- empty timeout response keeps the same cursor

**Do not add**
- ack tables
- WebSocket support
- public write route

## Task 2 — TeamRegistry trust boundary

**File**
- `apps/edge/src/worker_teams.ts`

**Implement**
- add `inbox_token_digest` to `agents`
- `POST /agents` accepts optional `inbox_token`
- or explicit `issue_inbox_token: true` to mint one once
- store only the digest
- add internal endpoints:
  - `GET /internal/agents/{agent_id}`
  - `POST /internal/agents/{agent_id}/verify_inbox_token`

**Contract**
- public reads do not expose token material
- inbox auth verification is internal-only

**Do not add yet**
- `notification_preference`
- `webhook_url`
- `auto_join_policy`

## Task 3 — Worker routing

**Files**
- `apps/edge/src/worker.ts`
- `apps/edge/wrangler.toml`

**Implement**
- add `AGENT_INBOXES` binding
- add migration for `AgentInboxDurableObject`
- route:
  - `GET /agents/{agent_id}/inbox`
- require bearer auth via TeamRegistry verification before proxying to inbox DO

**Do not implement**
- `POST /agents/{agent_id}/inbox`

## Task 4 — Room create invite fanout

**File**
- `apps/edge/src/worker.ts`

**Implement**
After `POST /rooms` succeeds:
- parse request `participants`
- parse returned top-level `invites` and `join_links`
- optionally read `created_by_agent_id`
- for each participant:
  - if `participant == created_by_agent_id`, set `creator_direct`
  - else verify agent exists in TeamRegistry
  - if invite token + join link exist, write `room_invite` to that participant inbox
  - record per-participant status in `invite_results`

**`room_invite` payload must include**
- `room_id`
- `participant`
- `invite_token`
- `join_link`
- `topic`
- `goal`
- `required_fields`
- `invited_by`
- `created_at_ms`

**Important**
Use top-level `responseBody.invites` and `responseBody.join_links`.
Do not read `room.join_link`.

## Task 5 — Tests

**Files**
- add / update focused contract tests under `apps/api/tests`

**Assert**
- inbox route is authenticated
- token digest exists in registry code
- internal verification endpoint exists
- room create invite fanout uses `participants`
- room create invite fanout reads top-level `invites` / `join_links`
- inbox DO uses epoch timestamps and long-poll semantics

## Task 6 — runnerd consumer (minimal landing)

**File**
- `apps/runnerd/src/runnerd/service.py`

**Implement**
- optional inbox poller thread, enabled only when env config is present
- long-poll `GET /agents/{agent_id}/inbox?after={cursor}&wait=30`
- bearer auth using `CLAWROOM_RUNNERD_INBOX_TOKEN`
- persist cursor locally to `~/.clawroom/runnerd/inbox_cursor.json`
- convert `room_invite` payload into a `WakePackage`
- submit it via the existing `wake()` path

**Current env contract**
- `CLAWROOM_RUNNERD_INBOX_AGENT_ID`
- `CLAWROOM_RUNNERD_INBOX_TOKEN`
- `CLAWROOM_RUNNERD_INBOX_RUNNER_KIND`
- `CLAWROOM_RUNNERD_INBOX_WAIT_SECONDS`
- `CLAWROOM_RUNNERD_DISPLAY_NAME`
- optional:
  - `CLAWROOM_RUNNERD_OWNER_LABEL`
  - `CLAWROOM_RUNNERD_GATEWAY_LABEL`

**Deliberate limits**
- presence sync reuses `POST /agents`; no separate presence protocol yet
- only `room_invite` is actionable in v1
- no webhook/WebSocket support
- no auto-join policy engine yet
- no full durable room scheduler yet

**Important**
- when runnerd spawns a bridge from inbox wake, it must preserve the stable inbox `agent_id` into the bridge join payload
- otherwise the room participant joins without `agent_id`, and later `ASK_OWNER` fanout has no trustworthy inbox target
- for `codex-bridge`, long blocking model calls must keep renewing the runner lease while generation is in progress
- otherwise the room sees `phase=reply_generating` but the attempt expires mid-turn (`lease_expired`) before the bridge can post its reply

## Task 7 — owner gate fanout (same inbox, no new transport)

**Files**
- `apps/edge/src/worker_room.ts`
- focused contract tests under `apps/api/tests`

**Implement**
- persist joined participant identity metadata:
  - `agent_id`
  - `runtime`
  - `display_name`
- when a participant sends `ASK_OWNER`:
  - keep existing `owner_wait` room semantics
  - additionally write `owner_gate_notification` into that participant agent inbox

**`owner_gate_notification` payload must include**
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

**Do not add**
- owner UI
- notification preferences
- webhook fanout
- auto-resolution policy

## Task 8 — local owner action surface (runnerd, still no UI)

**Files**
- `apps/runnerd/src/runnerd/service.py`
- `apps/runnerd/src/runnerd/app.py`
- focused contract tests under `apps/api/tests`

**Implement**
- expose pending owner gates from runnerd as a narrow local API:
  - `GET /owner-gates`
  - `GET /owner-gates/{owner_request_id}`
  - `POST /owner-gates/{owner_request_id}/reply`
- bridge replies by `owner_request_id` onto the existing run-level `submit_owner_reply(...)`
- persist and clear pending gates using the existing `pending_owner_gates.json`

**Contract**
- `GET /owner-gates` returns durable local pending gates only
- replying by `owner_request_id` must find the matching active run and append the reply to that run's owner reply file
- if no active matching run exists, return `404`

**Known nuance**
- after local reply submission, the run may still report `waiting_owner` until the bridge actually consumes the owner reply and clears `pending_owner_req_id`
- do not lie about that state in v1

## Verification

### Edge
- `npm run typecheck` in `apps/edge`
- focused pytest contract tests

### Manual smoke
1. register agent and capture returned `inbox_token`
2. create room naming that registered participant
3. long-poll `GET /agents/{id}/inbox?after=0&wait=0` with bearer token
4. verify returned `room_invite` includes executable `invite_token` + `join_link`
5. join that participant and send `ASK_OWNER`
6. verify `GET /agents/{id}/inbox?after=1&wait=0` returns `owner_gate_notification`
7. run local runnerd and confirm:
   - `GET /owner-gates` shows the pending owner gate
   - `POST /owner-gates/{owner_request_id}/reply` writes the owner reply onto the matching run
8. for managed Codex runs, confirm a long `reply_generating` turn does not lose its lease mid-generation

## Non-goals

Do not expand this plan into:
- transport preference registry fields
- webhook delivery
- WebSocket upgrade path
- full runtime scheduler
- marketplace/discovery product work


## Latest live truth (2026-03-17, owner-gate slice)

What is now proven locally against the live API:
- inbox-woken `codex-bridge` joins with a stable inbox `agent_id`
- normal room turns no longer treat `owner_context` as a fresh owner decision
- when the counterpart explicitly asks for an owner-only decision, the inbox-woken Codex path now emits `ASK_OWNER` first instead of jumping straight to `OWNER_REPLY`
- runnerd stores the resulting `owner_gate_notification` with a durable `run_id` fallback binding
- `POST /owner-gates/{owner_request_id}/reply` can resolve by that binding even before bridge state has refreshed `pending_owner_req_id`
- after local owner reply submission, the same bridge emits `OWNER_REPLY` and returns to `idle`

Concrete live proof:
- room `room_efe961302457`
- guest run `run_1fc110070117`
- bridge log shows:
  - `sent ASK_OWNER ...`
  - later `sent OWNER_REPLY ...`
- runnerd local API then reports:
  - `status=idle`
  - `reason=owner_reply_submitted`
  - `pending_owner_request=null`
  - `owner_gates=[]`

What this proves:
- the wake-up plane now handles both:
  - `room_invite -> inbox -> runnerd -> bridge join`
  - `ASK_OWNER -> owner_gate_notification -> local owner reply API -> OWNER_REPLY`

What it does **not** prove yet:
- a fully productized owner-facing UX
- a real external user completing this path unassisted
- Tier 2 codebase-attached collaboration beyond the current local bridge/runtime shell

## Latest Telegram helper-submitted truth (2026-03-17, owner-escalation lane)

What is now proven on the real cross-owner Telegram helper-submitted certified path:
- room `room_0bcca63d1928` passed cleanly
- scenario: `owner_escalation`
- result:
  - `status=closed`
  - `stop_reason=mutual_done`
  - `turn_count=6`
  - `execution_mode=managed_attached`
  - `runner_certification=certified`
  - `managed_coverage=full`
  - `product_owned=true`
- transcript shape:
  - host `ASK`
  - guest `ASK`
  - host `ASK_OWNER`
  - helper-submitted runnerd auto-detected the pending owner gate
  - helper owner reply was submitted through the local owner-gate surface
  - host emitted `OWNER_REPLY`
  - guest emitted `DONE`
  - host emitted `DONE`

Key implementation seam that had to be fixed for this proof:
- the Telegram harness could see `pending_owner_req_id` in bridge state but not in the runnerd `/runs/{id}` surface
- runnerd now backfills `pending_owner_request` from durable pending owner gates
- the harness now also consults `/owner-gates` as a fallback before auto-submitting owner replies

Important quality note:
- successful Telegram artifacts/history must not keep stale failure-time root-cause hints from the last live snapshot
- the harness now clears `primary_root_cause_*` on successful runs so clean passes stay diagnostically clean
