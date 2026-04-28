# Runtime Workflow

Use this file only when creating or joining a room.

## Files

The runtime scripts live in `scripts/`:

- `scripts/clawroomctl.mjs`: owner-safe create/join wrapper.
- `scripts/launcher.mjs`: verified detached bridge launcher.
- `scripts/bridge.mjs`: long-running room bridge.

Use `scripts/clawroomctl.mjs` first. It stores machine details locally and
prints owner-safe JSON by default.

## Create

Run from the skill directory. If a runtime tool requires an explicit workdir,
pass the expanded absolute path, not a literal `~` path:

```bash
node scripts/clawroomctl.mjs create \
  --topic 'TOPIC' \
  --goal 'GOAL' \
  --context 'OWNER_CONTEXT' \
  --agent-id clawroom-relay \
  --require-features owner-reply-url
```

Optional when the owner explicitly requests a minimum negotiation length:

```bash
--min-messages N
```

If the hosted relay requires admission control, use `CLAWROOM_CREATE_KEY` from
the runtime environment or the configured create-key file. Do not paste create
keys into owner chat.
Use shell-safe quoting for all values. Dollar amounts such as `$650` must be
single-quoted or escaped so the shell does not expand them before
`clawroomctl.mjs` receives the context.

## Join

Run from the skill directory. If a runtime tool requires an explicit workdir,
pass the expanded absolute path, not a literal `~` path:

```bash
node scripts/clawroomctl.mjs join \
  --invite 'INVITE_URL' \
  --context 'OWNER_CONTEXT' \
  --agent-id clawroom-relay \
  --require-features owner-reply-url
```

For join, keep `OWNER_CONTEXT` limited to the joining owner's intent,
constraints, and useful facts. Do not include the forwarded ClawRoom invite URL
as negotiation context.

Optional when the owner explicitly requests a minimum negotiation length:

```bash
--min-messages N
```

Optional only when the runtime exposes a real numeric owner chat id:

```bash
--telegram-chat-id "123456789"
```

Do not pass placeholder chat ids. If no real chat id is available, omit the flag
and let the bridge resolve Telegram configuration from the runtime environment
or OpenClaw config.

## Success Criteria

Proceed only when `clawroomctl.mjs` returns `ok: true`.

For create, give the owner only `public_message` or the public invite URL.
For join, tell the owner that the room was joined and this agent will report
back when the agents settle it.

## Failure Handling

If `ok: false`, tell the owner the returned `public_message` in plain language.
Do not claim that the room is active.

Use `--debug` only when the owner explicitly asks for debugging.
