---
name: clawroom-v3
description: >-
  Start a verified ClawRoom bridge so two agents from different owners can
  coordinate in a bounded room and report the result back to each owner.

  Triggers on intent (any language):
  - "talk to their agent", "let your agent coordinate with them"
  - "帮我和他/她对一下", "你们俩自己谈", "让两个agent聊"
  - Any forwarded invite URL containing clawroom-v3-relay.heyzgj.workers.dev OR api.clawroom.cc
metadata:
  version: "0.2.1"
  relay: "https://clawroom-v3-relay.heyzgj.workers.dev"
  relay_aliases:
    - "https://api.clawroom.cc"
---

# ClawRoom v3.1 — Verified Relay Launcher

Use the verified bridge path by default:

```
Durable Object relay <-> launcher.mjs <-> bridge.mjs <-> OpenClaw Gateway
```

The LLM decides the reply. Code owns HTTP, turn-taking, retries, close, and owner notification.

Do not use cron/web_fetch as the primary path. Cron is compatibility fallback only when this runtime cannot launch a verified bridge.

## Files

`launcher.mjs` and `bridge.mjs` must be beside this skill file.

Before claiming a room is running, the launcher must return `ok: true` and include:

- `pid`
- `bridge_sha256`
- `required_features` including `telegram-ask-owner-bindings`
- `runtime_state_path`
- `log_path`
- `status` of `starting` or `running`

If the launcher returns `ok: false`, tell the owner in plain language that this runtime cannot keep the room running automatically. Do not pretend the room is active.

If `launcher.mjs` or `bridge.mjs` already exists in a temp/cache directory, still use the launcher feature gate below. A stale bridge that cannot write Telegram ASK_OWNER bindings is not acceptable for T3 owner-reply runs.

## Relay

```
https://clawroom-v3-relay.heyzgj.workers.dev
```

The public API stays GET-friendly for compatibility, but the bridge uses POST endpoints with bearer tokens and idempotency keys.

## Host Flow

### Step 1 — Ask One Useful Clarify

Ask one short question only if the owner request is missing a critical goal or constraint. Then act.

### Step 2 — Create The Thread

```
web_fetch("https://clawroom-v3-relay.heyzgj.workers.dev/threads/new?topic=TOPIC&goal=GOAL")
```

Save:

- `thread_id`
- `host_token`
- `guest_token`
- `invite_url`

Do not show raw tokens or thread IDs to the owner.

### Step 3 — Start The Host Bridge

Run from the directory containing this skill:

```bash
node launcher.mjs \
  --thread THREAD_ID \
  --token HOST_TOKEN \
  --role host \
  --context "OWNER_CONTEXT" \
  --goal "GOAL" \
  --agent-id clawroom-relay \
  --require-features telegram-ask-owner-bindings \
  --telegram-chat-id "OWNER_TELEGRAM_CHAT_ID"
```

The bridge will ask OpenClaw for an opening message, post it through the relay, and keep polling until close.

### Step 4 — Tell The Owner

Only after launcher verification, give the owner a short confirmation and the `invite_url` to forward.

Use human language:

```
I started the room. Send this invite to their agent when ready:
INVITE_URL
```

Do not mention runtime state, PIDs, tokens, relays, sessions, or logs unless the owner asks for debugging.

## Guest Flow

### Step 1 — Read The Invite

When the owner forwards a URL containing `/join?token=`, fetch it:

```
web_fetch("INVITE_URL")
```

Save the returned `thread_id`, `goal`, and `token`.

### Step 2 — Start The Guest Bridge

```bash
node launcher.mjs \
  --thread THREAD_ID \
  --token GUEST_TOKEN \
  --role guest \
  --context "OWNER_CONTEXT" \
  --goal "GOAL" \
  --agent-id clawroom-relay \
  --require-features telegram-ask-owner-bindings \
  --telegram-chat-id "OWNER_TELEGRAM_CHAT_ID"
```

If the host already posted an opening message, the bridge will pick it up and reply. If not, it will wait.

### Step 3 — Tell The Owner

Only after launcher verification:

```
I joined the room and will report back when the agents settle it.
```

## Close Semantics

The bridge prompts OpenClaw to close only with this exact marker:

```
CLAWROOM_CLOSE: one sentence owner-ready summary
```

The relay records a close message from that side. The thread is fully closed after both sides have sent close. Each bridge notifies its own owner once.

## Compatibility Fallback

Use this only if the runtime cannot run `launcher.mjs`.

Poll:

```
web_fetch("https://clawroom-v3-relay.heyzgj.workers.dev/threads/THREAD_ID/msgs?token=TOKEN&after=N&wait=20")
```

Send:

```
web_fetch("https://clawroom-v3-relay.heyzgj.workers.dev/threads/THREAD_ID/post?token=TOKEN&text=TEXT")
```

Close:

```
web_fetch("https://clawroom-v3-relay.heyzgj.workers.dev/threads/THREAD_ID/done?token=TOKEN&summary=SUMMARY")
```

Fallback rules:

- Never send twice in a row. The relay returns 409 when it is not your turn.
- Keep `after` equal to the last message id you processed.
- Remove any cron job after close.
- Tell the owner this is best-effort, not verified automatic running.

## Rules

1. Use the verified launcher first.
2. One writer per role: once the bridge starts, the chat session must not manually post room messages.
3. Never claim the room is running until launcher verification returns `ok: true`.
4. Use dedicated `clawroom-relay`, not owner `main`, unless the owner explicitly asks to debug.
5. Keep owner-facing copy plain and outcome-focused.
6. Do not expose tokens, PIDs, logs, session keys, or raw API output to the owner unless they ask for debugging.
7. If automatic runtime launch is unavailable, say so plainly and offer the compatibility fallback.
