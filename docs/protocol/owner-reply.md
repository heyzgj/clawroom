# Owner Reply Protocol v0

Status: draft for ClawRoom v3.1 T3.
Scope: relay + bridge protocol for owner authorization during a room.

This protocol exists because owner authorization cannot be treated as a
prompt-only behavior. If an agent wants to exceed a mandate, or if the
bridge detects a mandate violation before close, the bridge must pause,
ask the owner, record the reply, and resume only after a valid reply.

## Surfaces

### v0 primary: tokenized owner-reply API

```
POST /threads/:id/owner-reply
Content-Type: application/json

{
  "token": "<owner_reply_token>",
  "question_id": "<question_id>",
  "role": "host",
  "text": "Do not go above 65000 JPY. Offer extra deliverables instead."
}
```

The bridge can place the same tokenized target in a Telegram notification
as a URL fallback:

```
/threads/:id/owner-reply?token=...&question_id=...&role=host&text=...
```

The GET fallback is intended for E2E/operator use and simple webform
experiments. Production UX should prefer Telegram reply routing once the
OpenClaw Telegram inbound handler can route replies safely.

### v1 target: Telegram reply routing

The owner replies to the ASK_OWNER notification message in Telegram. The
Telegram inbound handler recognizes `reply_to_message_id`, maps it to a
known `(thread_id, role, question_id)`, and POSTs to the same
`/threads/:id/owner-reply` endpoint.

Inbound routing must intercept these replies before the main OpenClaw
session sees them. Otherwise the owner reply can become a new instruction
to the main agent, repeating Lesson F2.

## ASK_OWNER Creation

The bridge creates a question by posting:

```
POST /threads/:id/ask-owner
Authorization: Bearer <host_or_guest_room_token>
Content-Type: application/json

{
  "text": "The guest asks for 73000 JPY, above your 65000 JPY ceiling. Approve or give a counter-instruction.",
  "idempotency_key": "ask-owner:..."
}
```

Relay response:

```
{
  "id": 6,
  "from": "host",
  "kind": "ask_owner",
  "text": "...",
  "ts": 1776221671000,
  "question_id": "q_...",
  "owner_reply_token": "owner_...",
  "expires_at": 1776223471000
}
```

The bridge then sends the owner a Telegram notification and enters
`waiting_owner`.

## Token Rules

- `owner_reply_token` is different from `host_token` and `guest_token`.
- It is scoped to exactly one `(thread_id, role, question_id)`.
- It is single-use.
- Default TTL is 30 minutes.
- A reply must cite both `question_id` and `role`.
- Replay, wrong role, wrong question, expired token, malformed payload,
  and missing text are all hard 4xx responses.

## Event Kinds

Relay transcript events are split into two classes:

- Turn events: `message`, `close`
- Control events: `ask_owner`, `owner_reply`

Turn-taking and close semantics only inspect turn events. Control events
are included in transcripts for validators and bridge resume logic, but
they must not let one agent post two `message`/`close` turns in a row.

## Waiting State

The relay records pending owner questions for token verification and
audit only. It does not own semantic waiting behavior.

The bridge owns `waiting_owner`:

- keep heartbeating,
- do not call OpenClaw,
- do not post normal messages,
- poll relay until a matching `owner_reply` event appears,
- then inject `OWNER_REPLY: <text>` into the next OpenClaw prompt and
  resume.

Persisted bridge state should include:

- `question_id`
- `owner_reply_token`
- `peer_message_id`
- `peer_text`
- `attempted_close_summary`
- `mandate_violation`
- `asked_at`
- `expires_at`

## Timeout Policy

Defaults:

- Owner reply TTL: 30 minutes.
- Reminder: not implemented in v0.
- T3 E2E timeout behavior: if owner reply never arrives before the test
  timeout, validator should fail and the failure artifact should be
  committed.

Future production behavior can add reminders and `owner_timeout` close
semantics, but v0 keeps the first working path narrow.

## Failure Responses

Recommended relay errors:

- `400 invalid_owner_reply`
- `400 text_required`
- `401 unauthorized_owner_reply`
- `404 question_not_found`
- `409 owner_reply_already_consumed`
- `410 owner_reply_expired`

Bridge behavior:

- Log the exact error code.
- Keep `waiting_owner` for retryable client/operator errors.
- Mark runtime `error` only when the relay cannot be reached or the
  question state is internally inconsistent.

## Mandate v0

The first mandate shape is intentionally narrow:

```
MANDATE: budget_ceiling_jpy = 65000
```

The bridge parses this from owner context and independently checks every
LLM `CLAWROOM_CLOSE:` summary. If the close summary contains a JPY amount
above the ceiling and there is no owner reply authorizing the exception,
the bridge must block the close, synthesize an ASK_OWNER, and wait.

The relay does not parse or enforce mandates.
