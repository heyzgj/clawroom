# ClawRoom v3.1 Real Telegram E2E

Goal: prove a real local OpenClaw Telegram bot and a real Railway-hosted OpenClaw Telegram bot can complete one room through the verified bridge path.

This is not a `railway run` test. `railway run` executes locally with Railway variables injected. A valid cross-machine test must show the Link bridge running inside the Railway service/container, while the clawd bridge runs on the local machine.

## Actors

- Host: local clawd Telegram OpenClaw
- Guest: Railway-hosted Link Telegram OpenClaw
- Relay: `https://clawroom-v3-relay.heyzgj.workers.dev`
- Runtime files: `clawroomctl.mjs`, `launcher.mjs`, `bridge.mjs`
- Dedicated OpenClaw agent: `clawroom-relay`

## Preconditions

Both runtimes must pass:

1. Node has built-in WebSocket:

   ```bash
   node -p "process.version + ' ' + typeof WebSocket"
   ```

   Expected: Node 22+ and `function`.

2. OpenClaw gateway is reachable from the same machine/container:

   ```bash
   node - <<'NODE'
   const ws = new WebSocket(process.env.OPENCLAW_GATEWAY_URL || 'ws://127.0.0.1:18789');
   ws.addEventListener('open', () => { console.log('gateway_ws_open'); ws.close(); });
   ws.addEventListener('error', (e) => { console.error('gateway_ws_error', e.message || e); process.exit(1); });
   setTimeout(() => process.exit(2), 5000);
   NODE
   ```

3. OpenClaw identity exists in that runtime. Use `OPENCLAW_STATE_DIR` when the runtime sets it; Railway Link uses `/data/.openclaw`.

   ```bash
   OPENCLAW_DIR="${OPENCLAW_STATE_DIR:-$HOME/.openclaw}"
   test -s "$OPENCLAW_DIR/identity/device.json"
   test -s "$OPENCLAW_DIR/identity/device-auth.json"
   ```

4. Dedicated relay agent exists:

   ```bash
   openclaw agents list | grep clawroom-relay
   ```

5. Telegram notification target is known:

   - `TG_BOT_TOKEN` must be available from env or `~/.openclaw/openclaw.json`
   - owner chat id must be passed as `--telegram-chat-id`

## Phase A: Operator-Assisted Remote Container Test

Use this first because it proves real cross-machine co-location without depending on Telegram skill-trigger behavior.

### 1. Deploy the relay

```bash
cd /Users/supergeorge/Desktop/project/clawroom-v3/relay
npm run deploy
```

### 2. Create a thread

```bash
curl -s "https://clawroom-v3-relay.heyzgj.workers.dev/threads/new?topic=calendar-sync&goal=Agree%20on%20one%2030min%20meeting%20time" | tee /tmp/clawroom-v3-thread.json
```

Record:

- `thread_id`
- `host_token`
- `guest_token`
- `invite_url`

### 3. Start local clawd as host

```bash
cd /Users/supergeorge/Desktop/project/clawroom-v3
node launcher.mjs \
  --thread "$THREAD_ID" \
  --token "$HOST_TOKEN" \
  --role host \
  --context "George can meet Wednesday 3pm Shanghai time for 30 minutes." \
  --goal "Agree on one 30 minute meeting time." \
  --agent-id clawroom-relay \
  --telegram-chat-id "$GEORGE_TELEGRAM_CHAT_ID"
```

Pass condition: launcher returns `ok: true`.

### 4. Start Link bridge inside Railway container

Do not use `railway run`.

Use Railway SSH from the dashboard or CLI. Railway docs say the CLI can start a shell session inside deployed services via `railway ssh`; copy the exact SSH command from the Railway dashboard if needed.

This is a diagnostic step, not the product path. It proves the Railway container can see Node, `OPENCLAW_STATE_DIR`, OpenClaw identity, the gateway, curl, and the dedicated agent before Telegram skill behavior is added on top.

Inside the Railway shell:

```bash
mkdir -p /tmp/clawroom-v3
cd /tmp/clawroom-v3

curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/launcher.mjs" -o launcher.mjs
curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/bridge.mjs" -o bridge.mjs
curl -fsSL "https://raw.githubusercontent.com/OWNER/REPO/BRANCH/clawroomctl.mjs" -o clawroomctl.mjs

node launcher.mjs \
  --thread "$THREAD_ID" \
  --token "$GUEST_TOKEN" \
  --role guest \
  --context "Tom can meet Wednesday afternoon except 4pm. Prefer English summary." \
  --goal "Agree on one 30 minute meeting time." \
  --agent-id clawroom-relay \
  --telegram-chat-id "$TOM_TELEGRAM_CHAT_ID"
```

Pass condition: launcher returns `ok: true` from inside Railway.

### 5. Observe room state

```bash
curl -s "https://clawroom-v3-relay.heyzgj.workers.dev/threads/$THREAD_ID/join?token=$HOST_TOKEN" | jq .
curl -s "https://clawroom-v3-relay.heyzgj.workers.dev/threads/$THREAD_ID/messages?token=$HOST_TOKEN&after=-1" | jq .
```

Expected:

- `runtime_heartbeats` contains host and guest
- `last_message` advances between host and guest
- final snapshot has `closed: true`
- `close_state.host_closed == true`
- `close_state.guest_closed == true`

### 6. Verify logs

Local:

```bash
cat ~/.clawroom-v3/$THREAD_ID-host.runtime-state.json
tail -100 ~/.clawroom-v3/$THREAD_ID-host.bridge.log
```

Railway:

```bash
cat "${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/clawroom-v3/$THREAD_ID-guest.runtime-state.json"
tail -100 "${OPENCLAW_STATE_DIR:-$HOME/.openclaw}/clawroom-v3/$THREAD_ID-guest.bridge.log"
```

Or use Railway logs. Railway docs say stdout/stderr are captured and can be viewed with `railway logs`.

Expected evidence:

- local host PID and Railway guest PID are different machines
- both use `agent_id: clawroom-relay`
- both use `session_key: agent:clawroom-relay:clawroom:<thread>:<role>`
- both show relay heartbeat success
- Railway side gateway URL resolves to local gateway from inside the container, typically `ws://127.0.0.1:18789`

## Phase B: Product Telegram Skill Test

Run this after Phase A passes.

1. Make `clawroomctl.mjs`, `launcher.mjs`, and `bridge.mjs` downloadable or bundled with the skill on both runtimes.
2. Send the host task to local clawd in Telegram.
3. Forward the generated invite to Link's owner / Link Telegram bot.
4. Link's OpenClaw skill must launch `launcher.mjs` inside the Railway container.
5. Validate the same relay state and log gates from Phase A.

Phase B passes only if no operator SSH command is used to start the guest bridge.

The v3 harness can create the thread, send both Telegram prompts through Telegram Desktop, and monitor the relay:

```bash
node /Users/supergeorge/Desktop/project/clawroom-v3/scripts/telegram_e2e.mjs \
  --host-bot @singularitygz_bot \
  --guest-bot @link_clawd_bot \
  --asset-base "https://YOUR_PUBLIC_ASSET_BASE" \
  --send \
  --monitor
```

If `--asset-base` is omitted, both bots must already have the v3.1 skill bundle installed locally. The harness writes prompt and state artifacts under `~/.clawroom-v3/e2e`.

## Pass Gates

The run passes only when all are true:

1. Relay is deployed as Durable Object, not KV.
2. Host launcher returns `ok: true`.
3. Guest launcher returns `ok: true` from inside Railway.
4. Both runtime heartbeats are visible in relay state.
5. Transcript alternates without same-role spam.
6. Thread reaches `closed: true`.
7. Both sides send close.
8. Each owner receives one final Telegram notification.
9. Railway logs or runtime state prove the Link bridge ran inside the Railway container.

## Fail Fast

Stop and fix before continuing if:

- `railway run` is the only remote execution proof
- `agentId` is `main`
- `runtime-state.json` is missing or has no relay heartbeat
- only one side closes
- a bridge starts but no heartbeat reaches relay
- final Telegram notification needs an active OpenClaw session
