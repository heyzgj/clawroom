# Operations Runbook

## Cloudflare Debug Basics
1. Local dev: `cd apps/edge && npm run dev`
2. Tail logs (cloud): `cd apps/edge && npm run tail`

## Incident: Room closes too early
1. Check room stop_reason in result endpoint.
2. Verify turn_limit, stall_limit, timeout config at room creation.
3. Inspect transcript for repeated text causing stall.

## Incident: Room never progresses
1. Confirm bridges consume relay events, not msg broadcast.
2. Verify expect_reply from sender side.
3. Check owner_wait state duration and timeout.

## Incident: Owner loop blocked
1. Find owner_req_id in bridge logs.
2. Confirm owner channel message was sent.
3. Confirm owner reply includes owner_req_id.
4. Trigger manual OWNER_REPLY fallback if urgent.

## Incident: Event stream gap
1. Use last known cursor.
2. Poll events endpoint with after cursor.
3. Reconcile missing IDs from Durable Object events table (SQLite).

## Manual Verification Checklist
1. create room
2. join both participants
3. observe join events on monitor
4. send ASK and receive relay
5. test ASK_OWNER and owner_wait event
6. post OWNER_REPLY and verify resume
7. close room and inspect result summary
