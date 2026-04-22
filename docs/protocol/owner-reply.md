# Owner Reply Protocol v0

Status: draft for ClawRoom v3.1 T3.
Scope: relay + bridge protocol for owner authorization during a room.

This protocol exists because owner authorization cannot be treated as a
prompt-only behavior. If an agent wants to exceed a mandate, or if the
bridge detects a mandate violation before close, the bridge must pause,
ask the owner, record the reply, and resume only after a valid reply.

## Surfaces

### v1 primary: ClawRoom-owned decision URL

The portable product path is a relay-hosted decision page:

```
GET /threads/:id/owner-reply?code=<owner_reply_code>
```

GET is non-mutating. It only renders a small noindex page with the question,
quick approve/reject buttons, and a counter-instruction textarea. The form then
submits:

```
POST /threads/:id/owner-reply
Content-Type: application/x-www-form-urlencoded

code=<owner_reply_code>&text=<owner decision>&source=owner_url
```

This path is owned by ClawRoom and does not require changes to OpenClaw,
Telegram inbound handlers, or a user-specific fork. The decision link is a
single-use magic link scoped to the question and protected by TTL. Link previews
may fetch the page, but they cannot record a decision because writes remain
POST-only.

### v0 machine API: tokenized owner-reply POST

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

This API remains useful for harnesses, bridge-local automation, and explicit
debug tooling. It must not be exposed as a raw owner-facing instruction unless
the owner asks for debugging.

`owner-reply` writes are intentionally POST-only. GET must not consume a token,
code, or event, because Telegram/link previews and other unfurlers may fetch
URLs automatically.

### optional adapter: Telegram reply routing

The owner replies to the ASK_OWNER notification message in Telegram. The
Telegram inbound handler recognizes `reply_to_message_id`, maps it to a
known `(thread_id, role, question_id)`, and POSTs to the same
`/threads/:id/owner-reply` endpoint.

Inbound routing must intercept these replies before the main OpenClaw session
sees them. Otherwise the owner reply can become a new instruction to the main
agent, repeating Lesson F2. This adapter is deployment-specific and is not a
ClawRoom core requirement.

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
  "owner_reply_url": "https://relay/threads/t_.../owner-reply?code=OR-...",
  "expires_at": 1776223471000
}
```

The bridge then sends the owner a Telegram notification with an "Open Decision
Page" URL button and enters `waiting_owner`.

## Token Rules

- `owner_reply_token` is different from `host_token` and `guest_token`.
- `owner_reply_code` is different from all room tokens and appears only in the
  owner decision URL.
- It is scoped to exactly one `(thread_id, role, question_id)`.
- It is single-use.
- Default TTL is 30 minutes.
- A machine API reply must cite both `question_id` and `role`.
- The relay MUST reject a POST whose `role` field does not exactly
  match the role recorded with the question. A host's
  `owner_reply_token` cannot answer a guest question, and vice versa.
  Mismatch returns `401 unauthorized_owner_reply`.
- A decision URL reply may cite only `code`; the relay resolves role and
  question from that code and rejects mismatches if extra role/question fields
  are supplied.
- Replay, wrong role, wrong question, expired token/code, malformed payload,
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
- `405 method_not_allowed`
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
