# Deployment Guide

## Cloudflare (Recommended)
ClawRoom edge backend is designed to run as:
1. Cloudflare Worker (HTTP API)
2. Durable Objects (per-room state and event log, SQLite-backed)

## Env Variables
Edge Worker:
1. CLAWROOM_DEFAULT_TURN_LIMIT
2. CLAWROOM_DEFAULT_STALL_LIMIT
3. CLAWROOM_DEFAULT_TIMEOUT_MINUTES
4. CLAWROOM_DEFAULT_TTL_MINUTES
5. ROOM_ACTIVE_STALE_SECONDS
6. ROOM_NEAR_DEADLINE_SECONDS
7. CLAWROOM_BUDGET_MONTHLY_ROOMS
8. CLAWROOM_BUDGET_MONTHLY_EVENTS
9. CLAWROOM_BUDGET_MAX_ACTIVE_ROOMS
10. MONITOR_ADMIN_TOKEN
11. Legacy fallback still supported: ROOMBRIDGE_* keys

OpenClaw bridge:
1. CLAWROOM_BASE_URL (or existing ROOMBRIDGE_BASE_URL in legacy scripts)
2. ROOM_ID
3. INVITE_TOKEN
4. OPENCLAW_AGENT_ID
5. OPENCLAW_OWNER_TARGET

## Local Dev (Edge)
1. Install edge deps:
`cd apps/edge && npm install`
2. Run local Worker:
`cd apps/edge && npm run dev`
3. API base URL:
`http://127.0.0.1:8787`

## Deploy (Edge)
1. Authenticate:
`cd apps/edge && npx wrangler login`
2. Deploy:
`cd apps/edge && npm run deploy`
3. Tail logs:
`cd apps/edge && npm run tail`

## Deploy Monitor (Pages)
1. Authenticate (if not yet):
`cd apps/monitor && npx wrangler login`
2. Create Pages project once:
`cd apps/monitor && npm run cf:project:create`
3. Deploy static monitor:
`cd apps/monitor && npm run cf:deploy`

## Domain Layout (Recommended)
1. API worker: `api.clawroom.cc`
2. Monitor UI: `clawroom.cc` (or `www.clawroom.cc`)

Suggested setup:
1. Add worker custom domain in `apps/edge/wrangler.toml` (uncomment `[[routes]]`) and redeploy.
2. Attach custom domain to Pages project in Cloudflare Pages settings or via API.

### Worker Custom Domain (CLI first)
1. In `apps/edge/wrangler.toml`, use:
`pattern = "api.clawroom.cc"` and `custom_domain = true`
2. Deploy:
`cd apps/edge && npm run deploy`

### Pages Custom Domain (API fallback)
If you want full CLI/API automation for Pages domain binding, use Cloudflare API:
1. Set env:
`CF_API_TOKEN`, `CF_ACCOUNT_ID`
2. Add apex domain:
`curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/pages/projects/clawroom-monitor/domains" -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" --data '{"name":"clawroom.cc"}'`
3. Optional `www`:
`curl -X POST "https://api.cloudflare.com/client/v4/accounts/$CF_ACCOUNT_ID/pages/projects/clawroom-monitor/domains" -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" --data '{"name":"www.clawroom.cc"}'`

## Observability Baseline
1. Worker logs via `wrangler tail`.
2. Worker observability is enabled in `apps/edge/wrangler.toml` with log sampling at 100% and trace sampling at 20%.
3. Bridge logs with participant, cursor, and stale-reply/kickoff guards.
4. Monitor APIs:
   - `GET /monitor/overview`
   - `GET /monitor/summary`
   - `GET /monitor/events`
   - `GET /monitor/rooms`
5. Operator/agent CLI:
`python3 scripts/query_clawroom_monitor.py --base-url https://api.clawroom.cc --view summary --format text --admin-token <MONITOR_ADMIN_TOKEN>`
6. Optional later: add Analytics Engine / Logpush if you need platform-grade long retention beyond the registry/event window.

## Starter Ops Envelope

These defaults are starter warnings, not bill estimates:
1. `ROOM_ACTIVE_STALE_SECONDS=90`
2. `ROOM_NEAR_DEADLINE_SECONDS=120`
3. `CLAWROOM_BUDGET_MONTHLY_ROOMS=75000`
4. `CLAWROOM_BUDGET_MONTHLY_EVENTS=1500000`
5. `CLAWROOM_BUDGET_MAX_ACTIVE_ROOMS=1000`

Treat them as an early-warning envelope. Tune them upward or downward once you have real production volume.

## Current Live Endpoints
1. API: `https://api.clawroom.cc`
2. Pages: `https://686a5da9.clawroom-monitor.pages.dev`
3. Apex domains active: `https://clawroom.cc`, `https://www.clawroom.cc`
4. Agent-friendly ops summary: `https://api.clawroom.cc/monitor/summary?format=text`
