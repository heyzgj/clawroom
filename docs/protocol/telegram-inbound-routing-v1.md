# Telegram Inbound Routing v1

Status: optional adapter spec. Not required for portable ClawRoom v3.1.

This spec defines an optional deployment-specific convenience path: a human
owner replies in Telegram to a ClawRoom `ASK_OWNER` notification, and the
OpenClaw Telegram inbound handler routes that decision to the ClawRoom relay
owner-reply endpoint instead of the main OpenClaw agent session.

The portable ClawRoom product path is now the relay-owned decision URL in
[`owner-reply.md`](owner-reply.md). That path does not require patching the
OpenClaw runtime or any bot source checkout. Use this inbound-routing spec only
when a specific deployment explicitly wants reply-to-message convenience.

Adapter implementation spans the ClawRoom repo plus the specific host runtime:

- `clawroom/bridge.mjs` writes the ASK_OWNER binding after Telegram `sendMessage` succeeds.
- the host runtime reads the binding before normal Telegram dispatch.
- `clawroom/relay/worker.ts` remains the authority for token, role, question, TTL, and single-use verification.

Telegram facts used: `sendMessage` returns the sent `Message`, including `message_id` and `chat.id`; incoming updates may include `message.reply_to_message.message_id`. References: <https://core.telegram.org/bots/api#sendmessage>, <https://core.telegram.org/bots/api#update>, <https://core.telegram.org/bots/api#message>.

## Goal

T3 v0 proved the relay owner-reply API by letting the harness POST the owner decision directly. The ClawRoom-owned decision URL is the portable product path. This adapter proves only an OpenClaw-specific convenience path:

1. bridge posts `ask_owner` to relay;
2. bridge sends an owner Telegram notification;
3. human owner replies to that Telegram message;
4. OpenClaw Telegram inbound recognizes the reply;
5. inbound handler POSTs `/threads/:thread_id/owner-reply`;
6. bridge sees `owner_reply` and resumes negotiation.

The owner should not see or copy tokens. Tokens may exist only inside a runtime-private local binding. If the runtime does not provide this adapter, the owner should use the ClawRoom decision URL instead.

## Binding Storage

Use option (a): filesystem bindings under the OpenClaw state directory.

```text
$OPENCLAW_STATE_DIR/clawroom-v3/ask-owner-bindings/
  <sha256(chat_id).slice(0,16)>.<message_id>.json
```

Why this choice:

- The bridge and Telegram inbound handler are co-located in both current deployments: local macOS clawd and Railway-hosted Link.
- The bridge already owns `thread_id`, `role`, `question_id`, and `owner_reply_token`, and receives Telegram `message_id` after `sendMessage`.
- The inbound handler later has `chat.id` and `reply_to_message.message_id`, so it can derive the same file key.
- No new local HTTP port, relay lookup endpoint, or relay knowledge of Telegram routing.

Rejected for v1:

- Bridge local HTTP endpoint: adds port, auth, lifecycle, and fails if the bridge exits before the owner replies.
- Relay-side binding lookup: works cross-process but widens the relay beyond the thin mailbox principle and expands secret surface.

The binding is a routing hint only. The relay still performs all authorization.

## Binding Schema

Each file is written atomically by the bridge:

```json
{
  "version": 1,
  "source": "clawroom_bridge",
  "relay": "https://clawroom-v3-relay.heyzgj.workers.dev",
  "thread_id": "t_...",
  "role": "host",
  "question_id": "q_...",
  "owner_reply_token": "owner_...",
  "telegram": {
    "chat_id_hash": "16hexchars",
    "chat_id_suffix": "1234",
    "message_id": 123
  },
  "created_at": "2026-04-15T00:00:00.000Z",
  "expires_at": 1776223471000,
  "consumed_at": null
}
```

Rules:

- `role` is exactly `host` or `guest`.
- `expires_at` matches relay's owner-reply TTL.
- Full `chat_id` may be used in memory but must not be logged or committed in artifacts.
- Filename is keyed by hashed `chat_id` plus `message_id`, because Telegram message ids are only unique within a chat.
- Multiple concurrent rooms are safe because each ASK_OWNER notification has a distinct `(chat_id, message_id)`.
- A recovery path may also scan for active bindings by `chat_id_hash` when Telegram reply metadata is missing. That fallback is only safe when exactly one unexpired binding exists for the chat.

Atomic write is: write `<name>.tmp`, fsync if helper support exists, then rename to `<name>.json`. TTL cleanup is opportunistic: lookup should delete expired/consumed bindings, and bridge may sweep old files on startup.

## Bridge Responsibility

After an ASK_OWNER notification succeeds:

```pseudo
delivery = telegramNotify(ask_owner_text)
if delivery.ok and delivery.message_id:
  write_binding({
    relay,
    thread_id,
    role,
    question_id,
    owner_reply_token,
    chat_id: delivery.chat_id or configured_chat_id,
    message_id: delivery.message_id,
    expires_at
  })
```

For current `clawroom/bridge.mjs`, this fits after `notifyOwnerQuestion()` records `telegram_message_id` in `waiting_owner`. The direct Bot API helper should retain both `result.message_id` and `result.chat.id`; response chat id wins over configured chat id.

If binding write fails, the room may still use the v0 tokenized POST fallback, but that run cannot claim T3 v1. Log a redacted error and keep waiting for a relay `owner_reply`.

## Runtime Patch Point

Do not assume every OpenClaw install has the same source tree, package layout,
or release cadence. Patch only the concrete running runtime that intentionally
opts into this adapter, and verify the deployed package, not a local source
checkout.

Patch at the earliest Telegram inbound message hook after the update has been
validated and before the runtime forwards the text into the main agent session.
The hook must run before text-fragment buffering, debounce queues, or any
`processMessage`-style main-agent dispatch.

## Routing Pseudocode

```pseudo
on telegram_update(update):
  msg = update.message
  if not msg:
    hand_to_main_agent(update)
    return

  if msg.reply_to_message:
    binding = lookup_ask_owner_binding(
      chat_id = msg.chat.id,
      message_id = msg.reply_to_message.message_id
    )
  else if looks_like_owner_authorization(msg.text) and exactly_one_active_binding_for_chat(msg.chat.id):
    binding = that_single_active_binding
    log_redacted("routed owner authorization without Telegram reply metadata")
  else if looks_like_owner_authorization(msg.text) and multiple_active_bindings_for_chat(msg.chat.id):
    send_telegram_confirm(msg.chat.id, "Please reply directly to the specific ClawRoom authorization question so I know which one this answers.")
    return
  else:
    hand_to_main_agent(update)
    return

  if binding is null:
    hand_to_main_agent(update)
    return

  if binding.expires_at <= now_ms():
    delete_binding(binding)
    send_telegram_confirm(msg.chat.id, "This authorization question expired. Ask your bridge to re-send it.")
    return

  if msg.text is empty:
    send_telegram_confirm(msg.chat.id, "Please reply with text so I can record your decision.")
    return

  response = POST `${binding.relay}/threads/${binding.thread_id}/owner-reply` {
    token: binding.owner_reply_token,
    question_id: binding.question_id,
    role: binding.role,
    text: msg.text,
    source: "telegram_inbound"
  }

  if response.ok:
    mark_binding_consumed(binding)
    send_telegram_confirm(msg.chat.id, "Authorization recorded.")
    return

  if response.status in [401, 409, 410]:
    mark_binding_consumed_if_terminal(response.status)
    log_redacted(response.status, response.error)
    send_telegram_confirm(msg.chat.id, `Could not record this decision (${response.error}). Ask the bridge to re-send it.`)
    return

  log_redacted("transient owner-reply failure")
  send_telegram_confirm(msg.chat.id, "Temporary error recording this decision. Please reply again in a moment.")
  return
```

`401`, `409`, and `410` are terminal for the current Telegram reply. Network errors, timeouts, and 5xx are retryable; keep the binding until TTL so the owner can reply again.

If the relay response was lost after a successful write and the retry receives `409 owner_reply_already_consumed`, treat it as success from the owner's point of view, consume the local binding, and confirm that authorization was already recorded.

## Lesson F2 Compliance

Intercepted ASK_OWNER replies MUST NOT be forwarded to the main OpenClaw agent session.

This is Lesson F2 in the v3 layer. A notification is not an instruction to the owner's main agent. Once inbound recognizes a message as an ASK_OWNER authorization decision, it must POST to the relay and return. It must not call `processMessage`, enqueue a normal inbound message, append it to the main session, or let the agent paraphrase it.

## Fall-Through And UX

Fall through to normal OpenClaw flow:

- update has no `message`;
- message has no `reply_to_message`, no active binding recovery match, and does not look like an owner authorization;
- reply target is not in the binding store;
- binding directory does not exist.

Do not fall through:

- non-reply text looks like an owner authorization and exactly one active binding exists for that chat;
- non-reply text looks like an owner authorization and multiple active bindings exist for that chat;
- binding exists but is expired;
- binding exists but relay returns `401`, `409`, or `410`;
- binding exists but owner reply is non-text;
- binding exists but relay network call times out or returns 5xx.

Mapping missing is safe only when there is no pending authorization signal: the owner may think they are chatting normally, and the message goes through normal OpenClaw flow. If there is exactly one active binding and the message looks like a decision, route it to owner-reply even without Telegram reply metadata. If there are multiple active bindings, do not guess; ask the owner to reply to the specific authorization message and return. Expired binding is different: send "This authorization question expired. Ask your bridge to re-send it." and do not forward. Relay `401`/`409` should log a redacted error code and tell the owner the decision could not be recorded.

Logs may include thread id, question id, status code, role, and hashed/suffixed chat id. Logs must not include Telegram bot token, owner-reply token, full chat id, or free-form owner text unless a local debug flag explicitly enables PII logs.

## Cross-Deployment Notes

Local clawd on macOS and Railway Link may share this path if they are running
that package version, but this is an implementation detail. State dir is local
`OPENCLAW_STATE_DIR`, or the default OpenClaw state dir for that user. Patch the
runtime's Telegram message handler before normal `processMessage` dispatch.

Railway-hosted Link on Linux uses the same plugin path in the container image. State dir must be persistent and shared with the self-launched bridge, normally Railway's `OPENCLAW_STATE_DIR` mounted volume. Verify the deployed image contains the patched build before T3 v1.

If a deployment uses a forked Telegram handler, it must provide an equivalent
pre-dispatch hook: lookup binding by `(chat_id, reply_to_message.message_id)`,
POST owner-reply, return. ClawRoom core must not depend on that hook existing.

## Relay Contract

Inbound handler POSTs:

```json
{
  "token": "owner_...",
  "question_id": "q_...",
  "role": "host",
  "text": "Approved up to 65000 JPY; do not exceed it.",
  "source": "telegram_inbound"
}
```

Relay must enforce: token belongs to this `question_id`; `role` exactly matches the role recorded with the question; token is not consumed; token is not expired; text is non-empty.

For optional adapter artifacts, `owner_reply` transcript rows should carry
`source: "telegram_inbound"`. Portable product-path artifacts should use the
owner decision URL and carry `source: "owner_url"`.

## Optional Adapter E2E Acceptance

Scenario: host context has budget ceiling `¥65,000` and must ask owner before
exceeding it; guest proposes `¥75,000` first; host bridge hits ASK_OWNER
instead of accepting over ceiling; human owner replies in Telegram to that
ASK_OWNER message; bot inbound routes the reply to relay owner-reply; bridge
resumes and reaches mutual close.

Pass criteria:

- validator green on existing room, close, runtime, turn-taking, and mandate checks;
- transcript includes matching `ask_owner` and `owner_reply`;
- `owner_reply` has `source: "telegram_inbound"`;
- no harness direct POST is used for the owner decision;
- intercepted Telegram reply is absent from the main OpenClaw session;
- failure artifacts are redacted and committed.

Reverse test: owner sends a normal Telegram message that is not a reply to an ASK_OWNER notification; bot routes it through normal OpenClaw flow; no owner-reply POST is attempted.

Recovery test: with exactly one active ASK_OWNER binding, owner sends a normal non-reply message such as "Approved again. You may accept JPY 75,000." Bot routes it to owner-reply with `source: "telegram_inbound"` and does not forward the text to the main agent.

Ambiguity test: with two active ASK_OWNER bindings in the same chat, owner sends a normal non-reply authorization. Bot asks the owner to reply to the specific authorization question; no owner-reply POST is attempted and the text is not forwarded to the main agent.

Expired-token test: owner replies after `expires_at`; bot says the question expired; bot does not forward the text to the main agent; relay creates no `owner_reply`; room does not close because of the stale reply.

## Open Questions

- Binding writer can live in `bridge.mjs` first; a shared OpenClaw helper can come later.
- Relay may initially store `source` as best-effort metadata rather than rejecting unknown source values.
- Web fallback should be a separate signed URL flow and is out of scope for v1.
