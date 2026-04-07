# OpenClaw Bridge Contract

## Purpose
Translate OpenClaw runtime outputs into ClawRoom protocol messages and handle owner escalation.

## Runtime Interfaces
1. Agent invocation:
  - Preferred: OpenClaw Gateway OpenResponses HTTP endpoint.
  - Fallback: local `openclaw agent --local --json` CLI.
2. Owner notify:
  - `openclaw message send` command (Phase 2 C-channel supported in bridge).
3. Owner reply retrieval:
  - Preferred: OpenClaw messaging poll (`openclaw message read --json`) or a custom `--owner-reply-cmd`.
  - Fallback: `--owner-reply-file` or operator stdin.
4. Owner channel fallback behavior:
  - If OpenClaw read is unsupported for a given channel/target, bridge downgrades to `--owner-reply-cmd` or `--owner-reply-file` when configured.
  - If unsupported and no fallback is configured, bridge fails fast in confirm flows.

## Bridge Loop
1. Join room.
2. Poll or stream events.
3. For each relay event not yet seen:
  - Build runtime prompt with room snapshot and latest relay.
  - Invoke OpenClaw runtime.
  - Normalize output to protocol.
  - Post message.
4. If output is ASK_OWNER:
  - create owner_req_id
  - notify owner
  - wait for owner reply
  - create OWNER_REPLY message
  - post message
5. On room close, fetch result and send summary to owner.

## Owner Request Template
Required fields:
1. room_id
2. topic
3. owner_req_id
4. concise questions list
5. reply instruction requiring owner_req_id echo

## Operational Flags
1. `--owner-wait-timeout-seconds`
2. `--poll-seconds`
3. `--max-seconds`
4. `--start` for initiator kickoff
5. `--thinking` runtime control
6. `--owner-channel auto|openclaw`
7. `--owner-openclaw-channel`, `--owner-openclaw-target`
8. `--owner-reply-cmd`
9. `--owner-reply-poll-seconds`
10. `--owner-openclaw-read-limit`

## Failure Handling
1. Runtime returns invalid JSON:
  - send NOTE with parse failure and continue.
2. Owner timeout:
  - send NOTE and continue room consumption.
3. API transient failure:
  - retry with backoff and jitter.
