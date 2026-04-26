# Relay Cutover Status - 2026-04-26

## Current Result

`api.clawroom.cc` is not yet reaching the v3 relay.

Verified:

```sh
node scripts/relay_target.mjs status
```

Result:

- `https://api.clawroom.cc` -> `404 not_found`, `looks_like_v3: false`
- `https://clawroom-v3-relay.heyzgj.workers.dev` -> `401 create_key_required`,
  `looks_like_v3: true`
- local `CLAWROOM_RELAY`: unset
- Railway `CLAWROOM_RELAY`: unset

Interpretation: runtime code already defaults to `api.clawroom.cc`, but the
custom domain is still bound to the wrong Worker or no v3 route. Do not run
production E2E against `api.clawroom.cc` until the probe returns `ok: true`.

## Changes Made

- `scripts/telegram_e2e.mjs` now defaults to `https://api.clawroom.cc`.
- `relay/wrangler.toml` now declares `api.clawroom.cc` as a Cloudflare Worker
  custom domain:

  ```toml
  [[routes]]
  pattern = "api.clawroom.cc"
  custom_domain = true
  ```

- Added `scripts/relay_target.mjs` for one-command status/probe/switch helpers.
- Added `docs/runbooks/RELAY_ENV_SWITCHING.md`.

## Blocker

Wrangler auth on this machine is invalid for deployment:

```text
Invalid access token [code: 9109]
```

`npx wrangler deploy --dry-run` validates the Worker bundle and bindings, but a
real deploy/custom-domain cutover requires a valid Cloudflare login or API
token with permission to deploy `clawroom-v3-relay` and manage the `clawroom.cc`
zone.

## Safe Current E2E Target

Until `api.clawroom.cc` probes healthy, run E2E against hosted fallback:

```sh
CLAWROOM_RELAY=https://clawroom-v3-relay.heyzgj.workers.dev \
  node scripts/telegram_e2e.mjs
```

or:

```sh
node scripts/telegram_e2e.mjs --relay https://clawroom-v3-relay.heyzgj.workers.dev
```

## Cutover Steps Left

1. Free `api.clawroom.cc` from any older Worker binding.
2. Authenticate Wrangler.
3. From `relay/`, run:

   ```sh
   npx wrangler deploy
   ```

4. Verify:

   ```sh
   node scripts/relay_target.mjs probe prod
   ```

5. If Railway has a temporary fallback override, switch it:

   ```sh
   node scripts/relay_target.mjs railway-set prod
   ```

6. Start fresh OpenClaw sessions before product-path E2E.
