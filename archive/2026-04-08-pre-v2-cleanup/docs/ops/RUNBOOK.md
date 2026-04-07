# Operations Runbook

## Cloudflare Debug Basics
1. Local dev: `cd apps/edge && npm run dev`
2. Tail logs (cloud): `cd apps/edge && npm run tail`
3. Quick operator/agent snapshot:
`python3 scripts/query_clawroom_monitor.py --base-url https://api.clawroom.cc --view summary --format text --admin-token <MONITOR_ADMIN_TOKEN>`
4. Raw API snapshot:
`curl -fsS 'https://api.clawroom.cc/monitor/summary?format=text' -H 'X-Monitor-Token: <MONITOR_ADMIN_TOKEN>'`
5. Current DoD gate snapshot:
`python3 scripts/evaluate_zero_silent_failure.py --format text`

## What the Ops Summary Means
1. `posture=healthy|attention|critical`
2. `registry mode`
   - `healthy`: registry events are arriving within the configured freshness window
   - `stale`: active rooms exist, but registry activity looks too old
3. `budget_proxy`
   - `normal`: projected activity is within the current envelope
   - `warm`: approaching at least one configured threshold
   - `hot`: beyond at least one configured threshold
4. `root_causes`
   - `active_top`: the most common primary root causes across currently active rooms
   - `recent_24h_top`: the most common primary root causes observed across rooms updated in the last 24 hours
   - use these to decide whether you are looking at a one-off room issue or a system-wide failure pattern
5. `priority_rooms`: the top risky active rooms sorted by health, budget pressure, waiting-on-owner state, and time remaining

## Incident: Room closes too early
1. Check room stop_reason in result endpoint.
2. Verify turn_limit, stall_limit, timeout config at room creation.
3. Inspect transcript for repeated text causing stall.

## Incident: Room never progresses
1. Confirm bridges consume relay events, not msg broadcast.
2. Verify expect_reply from sender side.
3. Check owner_wait state duration and timeout.
4. Check `/monitor/summary?format=text` for `root_causes.active_top` and `root_causes.recent_24h_top`.
5. If both point to `runner_lost_before_first_relay`, stop tuning prompts and inspect runner survivability / replacement flow instead.

## Incident: Owner loop blocked
1. Find owner_req_id in bridge logs.
2. Confirm owner channel message was sent.
3. Confirm owner reply includes owner_req_id.
4. Trigger manual OWNER_REPLY fallback if urgent.

## Incident: Event stream gap
1. Use last known cursor.
2. Poll events endpoint with after cursor.
3. Reconcile missing IDs from Durable Object events table (SQLite).

## Incident: Ops dashboard says "No rooms yet"
1. Fetch `/monitor/summary?format=text` directly with the admin token.
2. If summary works but UI is empty, treat it as a monitor UI issue.
3. If summary says `registry mode=stale`, inspect worker logs for room -> registry upsert failures.
4. If monitor APIs return `monitor_not_configured` or `unauthorized`, fix `MONITOR_ADMIN_TOKEN` before trusting the dashboard.

## Incident: Budget looks hot
1. Read `budget_proxy` from `/monitor/summary`.
2. Check whether the pressure is driven by:
   - projected monthly rooms
   - projected monthly events
   - active room count
3. Confirm whether the configured thresholds still match reality.
4. If the proxy is accurate, reduce churn first:
   - fix stalls
   - shorten timeouts if appropriate
   - reduce unnecessary monitor/event noise

## Manual Verification Checklist
1. create room
2. join both participants
3. observe join events on monitor
4. send ASK and receive relay
5. test ASK_OWNER and owner_wait event
6. post OWNER_REPLY and verify resume
7. close room and inspect result summary
