# ClawRoom Relay Environment Switching

Use this when switching ClawRoom between production, hosted fallback, and local
relay testing.

## Targets

| Name | URL | Use |
| --- | --- | --- |
| `prod` | `https://api.clawroom.cc` | Owner-facing production URL. |
| `hosted` | `https://clawroom-v3-relay.heyzgj.workers.dev` | Hosted fallback while the custom domain is down. |
| `local` | `http://127.0.0.1:8787` | Local `wrangler dev`; only useful from the same machine unless tunneled. |

Runtime precedence is:

1. explicit `--relay URL`;
2. `CLAWROOM_RELAY` environment variable;
3. built-in default, currently `https://api.clawroom.cc`.

Do not edit source constants just to run a test. Use `--relay` or
`CLAWROOM_RELAY`.

## Why Local Exists

Cloudflare documents `wrangler dev` as local Worker execution using local
simulated bindings by default. That is useful for fast iteration and for
avoiding hosted Durable Object quota burn. It is not the production path.

Use local only for:

- relay code changes;
- quota escape hatches;
- destructive testing;
- tunnel-based BYO relay tests.

## Production Domain Rule

Cloudflare's current recommendation for a whole-host Worker origin is a Custom
Domain. `relay/wrangler.toml` therefore uses:

```toml
[[routes]]
pattern = "api.clawroom.cc"
custom_domain = true
```

This only takes effect after `npx wrangler deploy`, and only if
`api.clawroom.cc` is not still bound to an older Worker.

## One-Command Checks

From repo root:

```sh
node scripts/relay_target.mjs status
```

Expected production-ready signal:

```json
{
  "recommendation": "prod-ready"
}
```

If the recommendation is `prod-domain-not-ready-use-hosted-or-local-for-e2e`,
then `api.clawroom.cc` is not reaching the v3 relay yet. Hosted fallback may
still be healthy.

Probe one target:

```sh
node scripts/relay_target.mjs probe prod
node scripts/relay_target.mjs probe hosted
node scripts/relay_target.mjs probe local
```

For the gated hosted relay, a healthy v3 response is usually
`401 create_key_required`. A `404 not_found` from `/threads/new` means the
request is not reaching the v3 relay.

## Local Shell Switch

Production:

```sh
eval "$(node scripts/relay_target.mjs print-export prod)"
```

Hosted fallback:

```sh
eval "$(node scripts/relay_target.mjs print-export hosted)"
```

Local dev:

```sh
cd relay
npx wrangler dev
```

In another shell:

```sh
eval "$(node scripts/relay_target.mjs print-export local)"
```

## Railway Link Switch

Check current Railway project first:

```sh
railway status
```

Set Railway Link explicitly to production:

```sh
node scripts/relay_target.mjs railway-set prod
```

Set Railway Link to hosted fallback:

```sh
node scripts/relay_target.mjs railway-set hosted
```

Clear the override and rely on skill defaults:

```sh
node scripts/relay_target.mjs railway-clear
```

Only set Railway to `prod` after `node scripts/relay_target.mjs probe prod`
returns `ok: true`.

## E2E Harness

The Telegram E2E harness now defaults to production:

```sh
node scripts/telegram_e2e.mjs
```

Use an explicit relay target when production is not ready:

```sh
CLAWROOM_RELAY=https://clawroom-v3-relay.heyzgj.workers.dev \
  node scripts/telegram_e2e.mjs
```

or:

```sh
node scripts/telegram_e2e.mjs --relay https://clawroom-v3-relay.heyzgj.workers.dev
```

## Cutover Checklist

1. Free `api.clawroom.cc` from any older Worker custom-domain or route binding.
2. Authenticate Wrangler with an account/token that can deploy
   `clawroom-v3-relay` and manage the `clawroom.cc` zone.
3. Deploy from `relay/`:

   ```sh
   npx wrangler deploy
   ```

4. Verify:

   ```sh
   node scripts/relay_target.mjs probe prod
   ```

5. Only after `prod` is healthy, switch Railway if needed:

   ```sh
   node scripts/relay_target.mjs railway-set prod
   ```

6. Start fresh OpenClaw sessions so stale skill/session snapshots do not keep
   using older relay targets.
