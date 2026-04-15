# Production URL Cutover Runbook — 2026-04-15

Goal: move the v3.1 relay from its development `workers.dev` subdomain
onto `https://api.clawroom.cc`, without breaking any E2E in flight.

Estimated wall-clock: 30–45 minutes (most of it is waiting for CF edge
propagation + re-running T3).

## State going in (as of commit `e87f544`)

- Relay is served from `https://clawroom-v3-relay.heyzgj.workers.dev`.
  This URL is hard-coded as `DEFAULT_RELAY` in `bridge.mjs:31` and
  `scripts/telegram_e2e.mjs:14`, and quoted throughout `SKILL.md` body.
- Bridge + harness already accept `--relay` CLI flag and
  `CLAWROOM_RELAY` env var overrides, so they can run against any URL
  without code changes.
- `SKILL.md` trigger keyword already recognizes BOTH
  `clawroom-v3-relay.heyzgj.workers.dev` and `api.clawroom.cc` as of
  v0.2.1. Invites using either URL will trigger the skill.
- `relay/wrangler.toml` has a commented-out `routes = [ ... ]` block
  ready to be uncommented at Step 2 below.
- `agent-chat/apps/edge/` currently owns `api.clawroom.cc` DNS via its
  v2 Worker deployment. That binding has to be removed (or the v3
  binding has to displace it) as part of Step 1.

## Prerequisites

Before starting:

- Cloudflare dashboard access for the account that holds the
  `clawroom.cc` zone and hosts the `clawroom-v3-relay` Worker.
- A local machine with `wrangler` authenticated
  (`npx wrangler whoami` returns the expected account).
- Both OpenClaw runtimes (local clawd + Railway Link) reachable; this
  runbook ends with a T3 E2E that needs both live.
- Git clean on `main` at or after commit `e87f544`.

## Step 1 — Remove (or disable) the v2 binding on `api.clawroom.cc`

The v2 Worker in `agent-chat/apps/edge/` currently answers at
`api.clawroom.cc`. CF will refuse to bind the same hostname to two
Workers at once.

Options:

- **1a (preferred)**: Delete the v2 Worker's custom-domain binding
  only. Dashboard path: Workers & Pages → (v2 worker name) →
  Triggers → Custom Domains → `api.clawroom.cc` → Delete. The v2
  Worker keeps existing on its `*.workers.dev` subdomain; only the
  custom hostname is freed up.
- **1b (cleaner but slower)**: Delete the v2 Worker entirely. Skip
  unless you've already decided to retire that Worker.

Verify: `dig api.clawroom.cc +short` no longer resolves to a CF IP
pointed at v2, OR the resolved CF IP returns 404 (depends on CF edge
cache). Worst case wait 1–2 minutes for propagation.

## Step 2 — Bind `api.clawroom.cc` to the v3 Worker

**Option A (preferred, version-controlled)**: Uncomment the block in
`relay/wrangler.toml`:

```toml
routes = [
  { pattern = "api.clawroom.cc/*", zone_name = "clawroom.cc" }
]
```

Then from `clawroom/relay/`:

```sh
npx wrangler deploy
```

**Option B (dashboard, if wrangler path fails)**: Dashboard →
Workers & Pages → clawroom-v3-relay → Triggers → Custom Domains →
Add Custom Domain → `api.clawroom.cc`.

Both paths do the same thing. Pick one; do not do both.

Verify:

```sh
curl -s "https://api.clawroom.cc/threads/new?topic=smoke&goal=smoke" | head -5
```

Expected: JSON response matching what
`https://clawroom-v3-relay.heyzgj.workers.dev/threads/new?...`
would return. If CF returns `1000`-series errors, wait 1–2 min for
edge propagation and retry.

## Step 3 — Flip `DEFAULT_RELAY` in the repo

```sh
cd ~/Desktop/project/clawroom
sed -i '' 's|https://clawroom-v3-relay\.heyzgj\.workers\.dev|https://api.clawroom.cc|g' \
  bridge.mjs scripts/telegram_e2e.mjs daemon.mjs 2>/dev/null || true
```

Note: `daemon.mjs` is untracked and can be included or skipped — it's
not part of the live bridge. Safe either way.

Also update `SKILL.md` body:

```sh
sed -i '' 's|https://clawroom-v3-relay\.heyzgj\.workers\.dev|https://api.clawroom.cc|g' \
  SKILL.md
```

Sanity: `grep -n 'workers.dev' SKILL.md bridge.mjs scripts/telegram_e2e.mjs`
should now only match historical lesson / progress references; no
runtime constants.

## Step 4 — Update README architecture diagram

`README.md:67` shows the relay URL in an ASCII diagram. Edit it to
`api.clawroom.cc`. This is purely cosmetic but worth keeping in sync.

## Step 5 — Commit

```sh
git add bridge.mjs scripts/telegram_e2e.mjs SKILL.md README.md relay/wrangler.toml
git commit -m "$(cat <<'EOF'
feat(prod-url): cut relay over to api.clawroom.cc

- wrangler.toml route binding uncommented (deploy side already done).
- DEFAULT_RELAY in bridge.mjs and telegram_e2e.mjs flipped.
- SKILL.md body URLs updated to api.clawroom.cc. Trigger keyword
  already accepted both URLs as of v0.2.1, so old cached invites
  still work.
- README architecture diagram updated.

Validated by post-cutover T3 E2E: docs/progress/v3_1_<room>.redacted.json
EOF
)"
```

## Step 6 — Re-upload gist test bundle

Any OpenClaw runtime that self-downloads `launcher.mjs` / `bridge.mjs`
from the gist at launch time will continue to get the OLD files until
you update the gist. Upload the post-Step-5 versions.

Specifically:

- `bridge.mjs` (new `DEFAULT_RELAY`)
- `launcher.mjs` (unchanged but upload both for consistency)

After upload, fetch a file from the gist via browser / curl to
confirm it's the new version.

## Step 7 — T3 E2E against the new URL

```sh
cd ~/Desktop/project/clawroom
node scripts/telegram_e2e.mjs --min-messages 4 --require-ask-owner
```

Expected: `t_<room>.json` artifact lands under `~/.clawroom-v3/e2e/`.
Validator should go all-green with all 12 checks.

If the host bridge's logs show it still hit `workers.dev`, something
is cached — check that the gist upload in Step 6 propagated and that
the Telegram launch prompt in `telegram_e2e.mjs` renders the fresh
bundle URL.

Redact the artifact and commit per the established discipline:

```sh
# redact host_token / guest_token / invite_url token / chat_id to REDACTED
# co-locate as docs/progress/v3_1_<room_id>.post_url_cutover.redacted.json
# add a lesson entry (next letter: AL) describing the cutover
# update the Updates Log line
# commit
```

## Rollback

If any step before Step 5 fails, no repo change has been committed;
just don't proceed.

If Step 5 is committed and Step 7 reveals a problem:

1. Revert the commit: `git revert HEAD`. Bridges using
   `DEFAULT_RELAY` will go back to `workers.dev`; any bridge already
   started with `--relay https://api.clawroom.cc` keeps running on
   that URL because it was explicit.
2. Optionally re-comment the `routes` block in wrangler.toml and
   redeploy, OR leave the CF binding in place (it doesn't harm
   anything; `api.clawroom.cc` will just return the same v3 relay
   that `workers.dev` returns).
3. Un-deploy the wrangler-side binding only if there's a reason to
   free the hostname: Dashboard → clawroom-v3-relay → Triggers →
   Custom Domains → api.clawroom.cc → Delete.

## Post-cutover cleanup (optional, do later)

- Consider turning off `workers.dev` subdomain for this Worker
  (wrangler.toml: `workers_dev = false`). Only do this after a week
  of clean operation on `api.clawroom.cc`, and only if you want to
  prevent accidental usage of the dev URL going forward.
- Update `docs/REAL_TELEGRAM_E2E.md` curl commands to use the new
  URL (not urgent; they still work against either).

## Known gotchas

- `wrangler deploy` will print a warning if the route pattern is
  already taken by another worker. Step 1 must run before Step 2.
- CF's automatic DNS record for a Custom Domain is a proxied A/AAAA
  record. If you have a pre-existing manual DNS record for
  `api.clawroom.cc` in the zone, it will conflict and the dashboard
  will ask you to delete it first. It is safe to delete; CF's own
  record will take over.
- `dig api.clawroom.cc` from a machine using Cloudflare WARP may
  return `198.18.x.x` instead of a public CF IP, per Lesson I.
  Don't use that as the verification oracle. Use `curl` which exits
  on HTTPS status, not on resolution.

## Success criteria (binary)

All of:

1. `curl https://api.clawroom.cc/threads/new?topic=...` returns valid
   thread JSON.
2. T3 E2E artifact exists, validator all-green.
3. Post-cutover artifact committed with a redaction preamble that
   references this runbook.
4. Bridge logs (in artifact or `~/.clawroom-v3/state/`) show
   requests going to `api.clawroom.cc`, not `workers.dev`.
