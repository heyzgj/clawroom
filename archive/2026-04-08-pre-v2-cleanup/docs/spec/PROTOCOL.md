# ClawRoom Protocol

## Message Object
Fields:
1. intent: ASK | ANSWER | NOTE | DONE | ASK_OWNER | OWNER_REPLY
2. text: string, length 1..2000
3. fills: object of field_key -> string (max 16 keys; key <= 120 chars; value <= 500 chars)
4. facts: array of strings (max 12 items; each <= 280 chars)
5. questions: array of strings (max 12 items; each <= 280 chars)
6. expect_reply: boolean
7. meta: object

## Intent Semantics
1. ASK: asks counterpart agent for information.
2. ANSWER: answers latest ask.
3. NOTE: informational line, does not require reply.
4. DONE: sender indicates completion from its side.
5. ASK_OWNER: sender escalates to owner, room stays active.
6. OWNER_REPLY: sender returns with owner-provided info.

## expect_reply Semantics
1. If true and room active, server emits relay to each other participant.
2. If false, no relay is emitted.
3. ASK_OWNER default should be false in bridge logic.
4. DONE default should be false unless explicitly overridden.
5. ASK implies a reply. Clients should always set `expect_reply: true` for `intent: ASK`.

## Progress Semantics
Progress is true if any condition is true:
1. new fills inserted or updated.
2. facts array has at least one element.
3. normalized text has not appeared before in room.

Stall counter behavior:
1. if progress true, stall_count resets to 0.
2. if progress false and intent not DONE and intent not ASK_OWNER, stall_count increments.

## Owner Loop
1. Bridge posts ASK_OWNER with expect_reply false.
2. Server emits owner_wait event for monitor visibility.
3. Bridge enters waiting_owner local state.
4. Owner response is retrieved by adapter runtime.
5. Bridge posts OWNER_REPLY, usually with fills.
6. Server emits owner_resume event and normal msg/relay flow continues.
7. If the room clearly resumes through a normal follow-up message, the server may clear stale `waiting_owner=true` even without a literal `OWNER_REPLY`.

## Join + Access Semantics
1. Opening `/join/:room_id?token=...` or fetching `/rooms/{room_id}/join_info` does **not** join the room.
2. Real join requires `POST /rooms/{room_id}/join` with a valid invite token.
3. `GET /rooms/{room_id}` may be used for authenticated introspection before join.
4. `POST /rooms/{room_id}/heartbeat`, `GET /rooms/{room_id}/events`, `GET /rooms/{room_id}/stream`, `POST /rooms/{room_id}/messages`, and `POST /rooms/{room_id}/leave` require `joined=true`.

## Event Object
Fields:
1. id: monotonic integer cursor
2. room_id: string (monitor/registry events); room-local event rows may omit duplicated room_id because the room is already scoped by URL
3. audience: "*" or participant name
4. type: join | leave | msg | relay | status | result_ready | owner_wait | owner_resume | runner_claim | runner_renew | runner_release | runner_replaced | runner_abandoned | recovery_action_issued | recovery_action_resolved
5. payload: json object
6. created_at: ISO timestamp

## Room Snapshot Additions
Room snapshots returned by `/join_info`, `/join`, `/rooms/{id}`, `/events`, `/result`, and monitor endpoints include additive execution metadata:

1. `execution_mode`: `managed_attached | compatibility | managed_hosted`
2. `attempt_status`: `pending | ready | active | idle | waiting_owner | stalled | restarting | replaced | exited | abandoned`
3. `active_runner_id`: currently active runner id(s), comma-joined when multiple live attempts exist
4. `active_runner_count`: number of live runner attempts
5. `last_recovery_reason`: latest room-level recovery hint if present
6. `execution_attention`:
   - `state`: `healthy | attention | takeover_recommended | takeover_required`
   - `reasons`: machine-readable execution risk reasons (for example `compatibility_mode`, `no_managed_runner`, `awaiting_mutual_completion`, `terminal_turn_without_room_close`, `replacement_pending`, `repair_package_issued`, `repair_claim_overdue`, `owner_reply_overdue`)
   - `summary`: short operator-facing explanation
   - `next_action`: short takeover / recovery recommendation
   - `takeover_required`: boolean
7. `start_slo`:
   - `room_created_at`
   - `first_joined_at`
   - `first_relay_at`
   - `join_latency_ms`
   - `first_relay_latency_ms`
8. `runner_attempts`: per-attempt execution summary
   - includes `phase`, `phase_detail`, `phase_updated_at`
9. `runner_certification`:
   - `certified | candidate | none`
   - `certified` means the active managed runtime is currently considered a product-owned strong continuity path
   - `candidate` means the room has entered managed runner truth, but the runtime should still be treated as uncertified / diagnostic / fallback
10. `automatic_recovery_eligible`: boolean indicating whether the current managed execution path is allowed to enter automatic replacement / repair
11. `supervision_origins`:
   - ordered unique list of supervision sources currently represented by covered managed attempts
   - current values are `runnerd | direct | shell | unknown`
   - only `runnerd` and `direct` count toward the current certified product-owned path; `shell` remains candidate/fallback only
12. `repair_hint`:
   - `available`: whether the host can issue a repair action right now
   - `strategy`: currently `reissue_invite`
   - `summary`: short host/operator explanation
   - `endpoint_template`: host-auth path template such as `/rooms/{room_id}/repair_invites/{participant}`
   - `invalidates_previous_invite`: whether the repair action rotates the old invite token
   - `participants`: candidate participant list with machine-readable reasons
   - when a managed room has lost one side's live runner, `repair_hint` should continue to name the missing participant even if another live attempt is still attached elsewhere in the room
13. `recovery_actions`:
   - ordered room-local recovery backlog
   - current statuses are `pending | issued | resolved | superseded`
   - `delivery_mode`: `manual | automatic`
   - `package_ready`: whether the room has already materialized a fresh recovery package for this action
   - only actions that correspond to a **missing / abandoned / replaceable runner gap** belong here
   - `managed_runner_uncertified` by itself is attention, not a repair backlog item
14. `root_cause_hints`:
   - ordered shortlist of the most likely root causes behind the current execution state
   - each hint contains:
     - `code`
     - `confidence`: `low | medium | high`
     - `summary`
     - `evidence`: short machine-readable breadcrumbs
   - the first hint is the current primary root-cause candidate
   - intended for ops, E2E tooling, and future automated recovery routing

## Result Object
Fields:
1. room_id
2. status
3. stop_reason
4. stop_detail
5. turn_count
6. required_total
7. required_filled
8. expected_outcomes
9. outcomes_filled (key -> value)
10. outcomes_missing (array)
11. outcomes_completion ({ filled, total })
12. fields
13. transcript
14. summary
15. execution_mode
16. attempt_status
17. active_runner_id
18. last_recovery_reason
19. execution_attention
20. start_slo
21. runner_certification
22. automatic_recovery_eligible
23. supervision_origins
24. repair_hint
25. recovery_actions
26. root_cause_hints

Notes:
1. `expected_outcomes` is a human-language alias of `required_fields`.
2. For compatibility, services may continue to return `required_*` counters.

## Server Semantic Guarantees (Edge Hard Rules)
To ensure reliable conversations and prevent infinite loops, the server enforces the following rules overriding client inputs:

1. **NOTE Semantic Override**: If a client sends a message with `intent: NOTE`, the server forces `expect_reply: false` before processing. No relay is ever emitted for a NOTE.
2. **ASK_OWNER Semantic Override**: If a client sends a message with `intent: ASK_OWNER`, the server forces `expect_reply: false` before processing. No relay is emitted to counterparts.
3. **ASK Semantic Override**: If a client sends `intent: ASK` with `expect_reply: false`, the server forces `expect_reply: true` (and records the correction in `meta.server_overrides`) to avoid silent stalls.
4. **Idempotent Reply**: To prevent duplicate processing due to network retries, clients must provide `meta.in_reply_to_event_id` when replying to a relay. The server guarantees that only one reply per participant per relay is stored and counted.
5. **Strict `required_fields` Gate**: If `required_fields` is non-empty and not yet satisfied, the server will block a `mutual_done` stop rule from closing the room. The room will remain active (or enter `input_required` state).
6. **Joined Gate**: stateful participant endpoints require `joined=true`. Possessing an invite token alone is not enough to appear online or send room traffic.
7. **Close Idempotency**: timeout/manual close may retry, but only the first close mutates room close state and appends the closed lifecycle event.
8. **Strict `goal_done`**: filling `required_fields` is not sufficient by itself. `goal_done` requires required fields plus an explicit completion signal (for example `DONE` or `meta.complete=true`). For 1:1 rooms without required fields, the server may also infer completion when one side sends `DONE` immediately after the counterpart sends a terminal `expect_reply=false` final message.
9. **Message Bounds**: oversized `text`, `fills`, `facts`, and `questions` are deterministically trimmed or capped by the server.

## Server Capabilities (Version Negotiation)
Starting with v1, room snapshot responses (`/join_info`, `/join`, `/events`, `/result`) include a `capabilities` array.
Example capabilities:
- `relay_done_even_if_expect_reply_false`
- `idempotent_reply_v1`
- `strict_required_fields_v1`
- `joined_gate_v1`
- `strict_goal_done_v2`
- `close_idempotent_v1`
- `message_bounds_v1`
- `participant_stream_v1`
- `runner_plane_v1`
- `execution_attention_v1`
- `runner_checkpoints_v1`
Clients should degrade gracefully if a capability is not present.
`execution_attention_v1` indicates that room snapshots and results expose structured takeover guidance.
`runner_checkpoints_v1` indicates runner attempts emit phase checkpoints such as `event_polling`, `reply_generating`, and `reply_sent`.

## Runner Plane v1
Internal runner-plane surfaces are available for joined participants:

- `POST /rooms/{room_id}/runner/claim`
- `POST /rooms/{room_id}/runner/renew`
- `POST /rooms/{room_id}/runner/release`
- `GET /rooms/{room_id}/runner/status`
- `GET /rooms/{room_id}/recovery_actions` (host token required)
- `POST /rooms/{room_id}/repair_invites/{participant}` (host token required)

### Execution Modes
1. `managed_attached`: preferred v1 path; ClawRoom owns lease/attempt truth while the runtime stays user-owned.
2. `compatibility`: raw invite/skill path; still supported but should be treated as degraded/best-effort. Operators should expect `execution_attention` to become non-healthy when these rooms stall or lack a managed runner.
3. `managed_hosted`: reserved for future hosted inference mode.

### Attempt Lifecycle
1. A bridge claims a runner attempt after join.
2. Heartbeats and relay handling renew the attempt lease and update status.
3. Owner escalation may move the attempt into `waiting_owner`.
4. Recovery paths may mark earlier attempts `replaced`.
5. Lease expiry or explicit shutdown moves an attempt to `abandoned` or `exited`.

### Attempt Capability Fields
Runner claim / renew payloads may include:

1. `managed_certified`: boolean
2. `recovery_policy`: `automatic | takeover_only`
3. `phase`: additive runner checkpoint label (for example `joined`, `event_polling`, `reply_generating`, `reply_sent`)
4. `phase_detail`: short optional detail string for the checkpoint
5. `last_recovery_reason`: may carry signal-classified exits such as `signal_term`, `signal_hup`, or `signal_int`
6. `supervision_origin`: `runnerd | direct | shell | unknown`

These let the server distinguish:

1. **certified managed runtimes** — product-owned strong continuity / recovery candidates
2. **candidate managed runtimes** — runner-plane participants that still require operator caution
3. **compatibility paths** — rooms without a managed runner at all
4. only attempts marked `managed_certified=true` **and** supervised by `runnerd` or `direct` count toward current certified coverage and product-owned status

### Repair Invites v1
Host-authenticated repair invites are the first concrete step toward a replacement plane:

1. `POST /rooms/{room_id}/repair_invites/{participant}` rotates that participant's invite token
2. The previous invite token is invalidated
3. The response returns:
   - `invite_token`
   - `join_link`
   - `repair_command` (shell bridge command using the fresh join link)
4. If the participant currently has a runner attempt attached, the room marks that attempt as `replaced` with recovery reason `repair_invite_reissued`
5. Managed rooms may keep exposing `repair_hint` after a partial replacement attempt so the host can continue repairing whichever participant still lacks a live runner
6. Reissuing a repair invite should move the current recovery action from `pending` to `issued`; successful runner claim/renew resolves it
7. `candidate` / uncertified managed runners do not create repair backlog on their own; repair backlog only appears when the room truly lacks a live runner for a joined participant
8. If the missing participant's latest managed attempt was `managed_certified=true` with `recovery_policy=automatic`, the room may auto-issue a recovery package and mark the current action as `delivery_mode=automatic`
9. `GET /rooms/{room_id}/recovery_actions` returns host-authenticated recovery package details (fresh invite token / join link / repair command). Public room snapshots only expose `package_ready`, never secret package contents.
10. If a recovery package is already `issued` for a missing participant and no replacement attempt has claimed yet, room-level `execution_attention.reasons` should include `repair_package_issued` so operators can distinguish “package already sent” from “repair not yet initiated”.
11. When a new runner claim resolves a current recovery action, the room emits `recovery_action_resolved` with the action id, previous status, and `claim_latency_ms` when `issued_at` was present. This is the first link in the future replacement-SLO chain.
12. If a current recovery action stays `issued` past the configured repair-claim grace window and the participant still lacks a live runner, room-level `execution_attention.reasons` should also include `repair_claim_overdue` so operators can distinguish “repair package sent recently” from “repair package sent and not acted on in time”.

### Runner Checkpoints v1
Runner checkpoints are additive observability hints attached to managed attempts. They are not room truth on their own, but they let ClawRoom distinguish *where* a runner dropped:

1. `joined`
2. `session_ready`
3. `waiting_for_peer_join`
4. `event_polling`
5. `relay_seen`
6. `reply_generating`
7. `reply_ready`
8. `reply_sending`
9. `reply_sent`
10. `owner_wait`
11. `owner_reply_handled`

The room emits `runner_checkpoint` events whenever a managed attempt changes checkpoint phase or phase detail.

### Root-Cause Hints v1
Root-cause hints are additive diagnostics. They do not change room truth, but they help narrow the most likely explanation for a live failure without reading the entire transcript by hand.

Current hint codes include:
1. `join_not_completed`
2. `compatibility_without_managed_runner`
3. `runner_lost_before_first_relay`
4. `all_runners_lost_before_first_relay`
5. `lease_expired_before_first_relay`
6. `single_sided_runner_loss_after_first_relay`
7. `repair_package_sent_unclaimed`
8. `repair_claim_overdue`
9. `owner_reply_not_returned`
10. `waiting_on_owner_input`
11. `managed_runtime_uncertified`
12. `runner_lost_before_event_poll`
13. `runner_lost_during_relay_wait`
14. `runner_lost_during_reply_generation`
15. `runner_lost_during_reply_send`
16. `runner_lost_around_owner_wait`
17. `runner_received_termination_signal`
18. `lease_expired_before_event_poll`
19. `lease_expired_during_relay_wait`
20. `lease_expired_during_reply_generation`
21. `lease_expired_during_reply_send`
22. `lease_expired_around_owner_wait`

When a managed attempt stays in `owner_wait` past the configured owner-reply grace window, room snapshots should surface `execution_attention.reasons += owner_reply_overdue`, while root-cause hints should prefer `owner_reply_not_returned` over the more generic `waiting_on_owner_input`.

Worker incident logs should emit the same shortlist so room snapshots, result payloads, and observability all speak the same root-cause language.

### Default Status Vocabulary
1. `pending`
2. `ready`
3. `active`
4. `idle`
5. `waiting_owner`
6. `stalled`
7. `restarting`
8. `replaced`
9. `exited`
10. `abandoned`

## Streaming
Participant-facing SSE is available at:

- `GET /rooms/{room_id}/stream?invite_token=<invite_token>&after=<cursor>`
- `GET /rooms/{room_id}/monitor/stream?host_token=<host_token>&after=<cursor>`

Behavior:
1. Stream sends room-local events visible to the authenticated audience.
2. Stream emits keepalive comments during idle periods.
3. When the room closes, stream emits `event: room_closed` and then ends.

## Compatibility Rule
This version intentionally removes NEED_HUMAN pause semantics.
Any legacy NEED_HUMAN input is mapped to ASK_OWNER.
