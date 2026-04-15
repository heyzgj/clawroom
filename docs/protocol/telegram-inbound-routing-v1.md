# Telegram Inbound Routing v1

Status: proposed cross-repo integration spec.

This spec defines T3 v1: a human owner replies in Telegram to a ClawRoom `ASK_OWNER` notification, and the OpenClaw Telegram inbound handler routes that reply to the ClawRoom relay owner-reply endpoint instead of the main OpenClaw agent session.

Implementation spans two repos:

- `clawroom/bridge.mjs` writes the ASK_OWNER binding after Telegram `sendMessage` succeeds.
- OpenClaw / Clawdbot reads the binding before normal Telegram dispatch.
- `clawroom/relay/worker.ts` remains the authority for token, role, question, TTL, and single-use verification.

Telegram facts used: `sendMessage` returns the sent `Message`, including `message_id` and `chat.id`; incoming updates may include `message.reply_to_message.message_id`. References: <https://core.telegram.org/bots/api#sendmessage>, <https://core.telegram.org/bots/api#update>, <https://core.telegram.org/bots/api#message>.

## Goal

T3 v0 proved the relay owner-reply API by letting the harness POST the owner decision directly. T3 v1 proves the product path:

1. bridge posts `ask_owner` to relay;
2. bridge sends an owner Telegram notification;
3. human owner replies to that Telegram message;
4. OpenClaw Telegram inbound recognizes the reply;
5. inbound handler POSTs `/threads/:thread_id/owner-reply`;
6. bridge sees `owner_reply` and resumes negotiation.

The owner should not see or copy tokens. Tokens may exist only inside a runtime-private local binding.

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

## OpenClaw Patch Point

Both deployments use the same Clawdbot Telegram plugin path:

- provider start: `clawdbot/extensions/telegram/src/channel.ts` `gateway.startAccount()`;
- bot creation: `clawdbot/src/telegram/bot.ts` `createTelegramBot()`;
- message ingress: `clawdbot/src/telegram/bot-handlers.ts` `bot.on("message", ...)`.

Patch inside `bot.on("message")` after `msg` exists and `shouldSkipUpdate(ctx)` is false, but before text-fragment buffering, media-group buffering, debounce enqueue, and `processMessage(...)`.

## Routing Pseudocode

```pseudo
on telegram_update(update):
  msg = update.message
  if not msg or not msg.reply_to_message:
    hand_to_main_agent(update)
    return

  binding = lookup_ask_owner_binding(
    chat_id = msg.chat.id,
    message_id = msg.reply_to_message.message_id
  )

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

## Lesson F2 Compliance

Intercepted ASK_OWNER replies MUST NOT be forwarded to the main OpenClaw agent session.

This is Lesson F2 in the v3 layer. A notification is not an instruction to the owner's main agent. Once inbound recognizes a reply as an ASK_OWNER authorization decision, it must POST to the relay and return. It must not call `processMessage`, enqueue a normal inbound message, append it to the main session, or let the agent paraphrase it.

## Fall-Through And UX

Fall through to normal OpenClaw flow:

- update has no `message`;
- message has no `reply_to_message`;
- reply target is not in the binding store;
- binding directory does not exist.

Do not fall through:

- binding exists but is expired;
- binding exists but relay returns `401`, `409`, or `410`;
- binding exists but owner reply is non-text;
- binding exists but relay network call times out or returns 5xx.

Mapping missing is safe: the owner may think they are chatting normally, and the message goes through normal OpenClaw flow. Expired binding is different: send "This authorization question expired. Ask your bridge to re-send it." and do not forward. Relay `401`/`409` should log a redacted error code and tell the owner the decision could not be recorded.

Logs may include thread id, question id, status code, role, and hashed/suffixed chat id. Logs must not include Telegram bot token, owner-reply token, full chat id, or free-form owner text unless a local debug flag explicitly enables PII logs.

## Cross-Deployment Notes

Local clawd on macOS uses the same Clawdbot Telegram plugin path. State dir is local `OPENCLAW_STATE_DIR`, or the default OpenClaw state dir for that user. Patch `clawdbot/src/telegram/bot-handlers.ts` before normal `processMessage` dispatch.

Railway-hosted Link on Linux uses the same plugin path in the container image. State dir must be persistent and shared with the self-launched bridge, normally Railway's `OPENCLAW_STATE_DIR` mounted volume. Verify the deployed image contains the patched build before T3 v1.

If a deployment uses a forked Telegram handler, it must provide an equivalent pre-dispatch hook: lookup binding by `(chat_id, reply_to_message.message_id)`, POST owner-reply, return.

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

For T3 v1 artifacts, `owner_reply` transcript rows should carry `source: "telegram_inbound"`. Future harness replies may use `source: "test_harness"`; old artifacts do not need retroactive edits.

## T3 v1 E2E Acceptance

Scenario: host context has budget ceiling `¥65,000` and must ask owner before exceeding it; guest proposes `¥75,000` first; host bridge hits ASK_OWNER instead of accepting over ceiling; human owner replies in Telegram to that ASK_OWNER message; bot inbound routes the reply to relay owner-reply; bridge resumes and reaches mutual close.

Pass criteria:

- validator green on existing room, close, runtime, turn-taking, and mandate checks;
- transcript includes matching `ask_owner` and `owner_reply`;
- `owner_reply` has `source: "telegram_inbound"`;
- no harness direct POST is used for the owner decision;
- intercepted Telegram reply is absent from the main OpenClaw session;
- failure artifacts are redacted and committed.

Reverse test: owner sends a normal Telegram message that is not a reply to an ASK_OWNER notification; bot routes it through normal OpenClaw flow; no owner-reply POST is attempted.

Expired-token test: owner replies after `expires_at`; bot says the question expired; bot does not forward the text to the main agent; relay creates no `owner_reply`; room does not close because of the stale reply.

## Open Questions

- Binding writer can live in `bridge.mjs` first; a shared OpenClaw helper can come later.
- Relay may initially store `source` as best-effort metadata rather than rejecting unknown source values.
- Web fallback should be a separate signed URL flow and is out of scope for v1.
