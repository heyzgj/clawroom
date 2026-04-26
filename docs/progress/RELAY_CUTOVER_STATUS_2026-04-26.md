# Relay Cutover Status - 2026-04-26

## Current Result

`api.clawroom.cc` now reaches the v3 relay.

Verified:

```sh
node scripts/relay_target.mjs status
```

Result:

- `https://api.clawroom.cc` -> `401 create_key_required`,
  `looks_like_v3: true`
- `https://clawroom-v3-relay.heyzgj.workers.dev` -> `401 create_key_required`,
  `looks_like_v3: true`
- local `CLAWROOM_RELAY`: unset
- Railway `CLAWROOM_RELAY`: unset
- recommendation: `prod-ready`

Interpretation: runtime code defaults to `api.clawroom.cc`, the custom domain
is bound to `clawroom-v3-relay`, and the hosted relay is still protected by the
private-beta create-key gate. `401 create_key_required` is the expected healthy
unauthenticated probe response.

## Changes Made

- `scripts/telegram_e2e.mjs` now defaults to `https://api.clawroom.cc`.
- `relay/wrangler.toml` now declares `api.clawroom.cc` as a Cloudflare Worker
  custom domain:

  ```toml
  [[routes]]
  pattern = "api.clawroom.cc"
  custom_domain = true
  ```

- `relay/wrangler.toml` explicitly keeps the hosted fallback enabled:

  ```toml
  workers_dev = true
  ```

- Added `scripts/relay_target.mjs` for one-command status/probe/switch helpers.
- Added `docs/runbooks/RELAY_ENV_SWITCHING.md`.

## Deployment

Wrangler OAuth login was refreshed on this machine and the Worker was deployed
from `relay/`.

```sh
npx wrangler login
npx wrangler deploy
```

Latest deployed version:

```text
b63236ea-f2d1-42a7-89c3-c55d4edbfb4d
```

Triggers:

```text
https://clawroom-v3-relay.heyzgj.workers.dev
api.clawroom.cc
```

## Current E2E Target

Use production by default:

```sh
node scripts/telegram_e2e.mjs
```

Use hosted fallback only when testing cutover/fallback behavior:

```sh
node scripts/telegram_e2e.mjs --relay https://clawroom-v3-relay.heyzgj.workers.dev
```

## Steps Left

1. Start fresh OpenClaw sessions before product-path E2E so stale skill/session
   snapshots do not keep older relay targets.
2. Run one production URL product-path E2E and commit the redacted artifact.
3. Keep `workers_dev = true` only while hosted fallback is useful; later decide
   whether production should be custom-domain only.

5. If Railway has a temporary fallback override, switch it:

   ```sh
   node scripts/relay_target.mjs railway-set prod
   ```

6. Start fresh OpenClaw sessions before product-path E2E.
