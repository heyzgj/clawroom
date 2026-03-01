# Design Agent Brief: ClawRoom Monitor v2

## Purpose
Build the production-quality monitor UI for ClawRoom.
Current embedded monitor in API is engineering-grade only and should be replaced by a standalone design-led experience.

## Product Context
ClawRoom is a neutral meeting room where two agents exchange messages with bounded stop rules.
Users need confidence, clarity, and quick understanding of what happened in the room.

## User Goals
1. Know room status instantly: active or closed, and why.
2. See progress against required fields.
3. Observe conversation flow in real time.
4. Understand owner escalation events clearly.
5. Read final result quickly without opening raw JSON.

## Scope
1. Standalone monitor app under `apps/monitor/`.
2. Responsive layout for desktop and mobile.
3. Real-time event feed using SSE, with poll fallback.
4. Progressive disclosure: human summary first, raw data optional.

## Non-goals
1. Do not redesign backend API contracts.
2. Do not add auth or billing surfaces.
3. Do not implement Slack or Telegram clients.

## Required Information Architecture
1. Header:
   - room id
   - room topic
   - status badge
   - updated timestamp
2. Left panel:
   - participants list with `online`, `done`, `waiting_owner`
   - required fields and fill status
   - final summary card
3. Main panel:
   - timeline stream (join, msg, relay, owner_wait, owner_resume, leave, status)
   - transcript mode toggle
4. Optional debug drawer:
   - raw payload JSON

## Required Event Semantics to Represent
1. `join`
2. `leave`
3. `msg`
4. `relay`
5. `owner_wait`
6. `owner_resume`
7. `status`
8. `result_ready`

## Data Endpoints
1. `GET /rooms/{room_id}/monitor/events?host_token=...&after=...&limit=...`
2. `GET /rooms/{room_id}/monitor/stream?host_token=...&after=...`
3. `GET /rooms/{room_id}/monitor/result?host_token=...`

## UX Principles
1. Human-first language, not protocol-heavy wording.
2. Show confidence signals:
   - clear status
   - progress completion
   - explicit stop reason
3. Reduce cognitive load:
   - grouped timeline cards
   - intent chips
   - concise event labeling
4. Progressive disclosure for technical details.

## Visual Direction
1. Avoid generic dashboard style.
2. Use strong but calm visual identity.
3. Make event hierarchy obvious at a glance.
4. Keep accessibility:
   - keyboard navigable
   - visible focus states
   - contrast-safe colors
   - reduced-motion mode

## Interaction Requirements
1. Live updates auto-append events.
2. Sticky status region always visible.
3. Filters:
   - all events
   - conversation only
   - owner loop only
4. Search in transcript text.
5. Export result/transcript JSON button.

## States to Design
1. Empty/loading
2. Active room
3. Waiting owner (one participant)
4. Closed with `goal_done`
5. Closed with `manual_close`
6. Error/reconnect mode

## Delivery Format
1. High-fidelity screens for desktop and mobile.
2. Component spec with spacing, typography, color tokens, interaction notes.
3. Handoff checklist mapping components to API data fields.

## ClawRoom Monitor Implementation Notes (Current State)
The `apps/monitor/` implementation is live-data ready:
1. URL config parsing via `room_id` and `host_token`.
2. SSE-first event client with polling fallback and reconnect backoff.
3. Reduced-motion support via `prefers-reduced-motion`.
4. Display font (`Outfit`) loaded in `index.html`.

Remaining polish:
1. Optional compact debug mode (`?debug=1`) for payload troubleshooting.
2. Optional transcript search/filter UX pass.

## Acceptance Criteria
1. A first-time user can tell room outcome in under 5 seconds.
2. A first-time user can identify which side asked owner and when.
3. Timeline is readable on 390px mobile width.
4. All required states above are covered in final design file.
