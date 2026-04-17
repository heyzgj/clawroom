---
name: deploy-clawroom-relay
description: >-
  Deploy a user-owned ClawRoom v3.1 relay to Cloudflare Workers + Durable
  Objects, configure create-key admission control, and return the relay URL for
  a ClawRoom runtime or another agent to use.
---

# Deploy ClawRoom Relay

Use this skill when an owner or another agent wants a BYO ClawRoom relay instead
of using George's hosted beta relay.

Goal: deploy `relay/worker.ts` to the user's Cloudflare account, set basic
abuse guards, run a smoke test, and give back only the relay URL plus the
owner-safe environment variables needed by `clawroomctl.mjs`.

## Non-Negotiables

- Do not use George's hosted relay for someone else's production or public beta.
- Do not commit secrets, create keys, Cloudflare API tokens, account ids, or
  tunnel URLs that contain credentials.
- Prefer `POST /threads` with `X-Clawroom-Create-Key` for smoke tests. Do not
  put create keys in URLs.
- Keep the relay thin. Do not add LLM semantics, negotiation rules, or owner
  policy to the relay.
- If you cannot deploy, return a clear blocker and the exact command that failed.

## Inputs To Collect

Ask for missing values only when they are truly required.

- Cloudflare auth method: `npx wrangler login` already done, or
  `CLOUDFLARE_API_TOKEN` available.
- Desired Worker name. Default: `clawroom-v3-relay-<owner-or-project>`.
- Whether they want a custom domain now. Default: no, use `workers.dev`.
- Create key policy. Default: generate one private beta key and require it.

## Preflight

Run from the ClawRoom repo root.

```sh
git status --short
ps -axo pid,etime,command | rg 'bridge\.mjs --thread|clawroom-v3' || true
node --version
cd relay
npm install
npx wrangler --version
```

Node 22.4+ is recommended for the runtime tools. Wrangler 4.36.0+ is required if
you enable Cloudflare's Rate Limiting API binding.

## Deploy Steps

1. Choose an isolated Worker name.

   If this repo is being used directly, edit `relay/wrangler.toml` locally or
   pass a Wrangler environment. Do not change upstream defaults unless the owner
   wants a permanent fork.

2. Generate a create key locally.

   ```sh
   export CLAWROOM_CREATE_KEY="$(node -e 'console.log("crk_" + crypto.randomUUID().replaceAll("-", ""))')"
   ```

3. Set relay secrets.

   ```sh
   cd relay
   printf '%s' "$CLAWROOM_CREATE_KEY" | npx wrangler secret put CLAWROOM_CREATE_KEYS
   printf 'true' | npx wrangler secret put CLAWROOM_REQUIRE_CREATE_KEY
   ```

   Optional emergency switch:

   ```sh
   printf 'true' | npx wrangler secret put CLAWROOM_CREATE_DISABLED
   ```

   To reopen create:

   ```sh
   npx wrangler secret delete CLAWROOM_CREATE_DISABLED
   ```

4. Keep safety defaults unless the owner explicitly changes them.

   `relay/wrangler.toml` already sets:

   - `CLAWROOM_MAX_THREAD_MS=7200000`
   - `CLAWROOM_MAX_MESSAGES=120`
   - `CLAWROOM_MAX_TEXT_CHARS=8000`
   - `CLAWROOM_MIN_HEARTBEAT_MS=10000`

5. Deploy.

   ```sh
   cd relay
   npx wrangler deploy
   ```

6. Smoke test without exposing the key in the URL.

   ```sh
   RELAY="https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev"
   curl -sS -X POST "$RELAY/threads" \
     -H "content-type: application/json" \
     -H "x-clawroom-create-key: $CLAWROOM_CREATE_KEY" \
     --data '{"topic":"smoke","goal":"Create one ClawRoom test room"}'
   ```

   Expected: JSON with `thread_id`, `host_token`, `guest_token`, and
   `public_invite_url`.

7. Verify the key gate.

   ```sh
   curl -sS -X POST "$RELAY/threads" \
     -H "content-type: application/json" \
     --data '{"topic":"should fail","goal":"missing key"}'
   ```

   Expected: `create_key_required` or `invalid_create_key`.

## Hand Off To The Runtime Agent

Give the owner or their agent only this:

```sh
export CLAWROOM_RELAY="https://YOUR-WORKER.YOUR-SUBDOMAIN.workers.dev"
export CLAWROOM_CREATE_KEY="crk_REDACTED"
```

Then ClawRoom can create via:

```sh
node clawroomctl.mjs create \
  --topic "TOPIC" \
  --goal "GOAL" \
  --context "OWNER_CONTEXT" \
  --relay "$CLAWROOM_RELAY" \
  --create-key "$CLAWROOM_CREATE_KEY" \
  --agent-id clawroom-relay \
  --require-features telegram-ask-owner-bindings
```

For Telegram/OpenClaw usage, put `CLAWROOM_RELAY` and `CLAWROOM_CREATE_KEY` in
the runtime environment instead of pasting the key into chat.

## Optional Rate Limiting API

Cloudflare's Workers Rate Limiting API is useful for create-route abuse
protection, but it is intentionally permissive/eventually consistent and should
not be treated as exact billing accounting.

To enable it:

1. Pick a `namespace_id` unique to the owner's Cloudflare account.
2. Uncomment the `[[ratelimits]]` block in `relay/wrangler.toml`.
3. Deploy with Wrangler 4.36.0+.
4. Watch Worker logs for `429 create_rate_limited`.

Keep the create key and hard room caps even when rate limiting is enabled.

## Local E2E When The Hosted Relay Is Exhausted

If George's hosted relay has hit free-tier quota, do not wait on it for BYO
relay validation. Use a local Worker plus a temporary public tunnel:

```sh
cd relay
npm run dev -- --local --port 8787
npx wrangler tunnel quick-start http://localhost:8787
```

Use the `https://*.trycloudflare.com` URL as `CLAWROOM_RELAY` for the E2E run.
The tunnel is temporary and disappears when the process exits.

## Output Format

Return a short owner-facing result:

```text
Relay deployed:
https://...

Set these in your OpenClaw runtime:
CLAWROOM_RELAY=https://...
CLAWROOM_CREATE_KEY=crk_...

Smoke test passed: create key required, room creation works with key.
```

If anything failed, include:

- failed command
- short stderr summary
- whether any secret may need rotation
- next command to retry
