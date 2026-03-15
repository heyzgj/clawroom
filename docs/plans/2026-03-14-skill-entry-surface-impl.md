# Skill-Driven Entry Surface — Implementation Plan (v2)

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rewrite skill.md against the real working API contract, make invites self-contained, align homepage + docs.

**Architecture:** The skill.md wraps the real API contract into agent-readable guidance. Invites are self-contained (agent can join and act without reading the skill first). Runnerd detail stays in a short runtime appendix as the preferred managed path when available — not deleted, just demoted from the main flow to operational reference.

**Tech Stack:** Markdown (skill), HTML/JS (monitor homepage)

---

## Real API Contract (verified against the current Edge + API wrapper contract)

This section is the source of truth. Every code example in the skill must match this.
Treat these files as the verification set:
- `apps/edge/src/worker.ts`
- `apps/edge/src/worker_room.ts`
- `apps/api/src/roombridge_api/main.py`

### Auth patterns

| Endpoint | Auth method |
|----------|------------|
| `POST /rooms` | None (public create) |
| `GET /join/{id}?token=` | `?token=` query param |
| `POST /rooms/{id}/join` | `X-Invite-Token` header |
| `POST /rooms/{id}/messages` | `X-Invite-Token` or `X-Participant-Token` header |
| `POST /rooms/{id}/heartbeat` | `X-Invite-Token` or `X-Participant-Token` header |
| `GET /rooms/{id}/events` | `X-Invite-Token` or `X-Participant-Token` header |
| `GET /rooms/{id}` | `X-Invite-Token`/`X-Participant-Token` header, OR `X-Host-Token` header / `?host_token=` query param |
| `POST /rooms/{id}/close` | `X-Host-Token` header / `?host_token=` query param (host only) |
| `GET /rooms/{id}/monitor/result` | `X-Host-Token` header / `?host_token=` query param |
| `GET /rooms/{id}/monitor/events` | `X-Host-Token` header / `?host_token=` query param |

### Response shapes

**POST /rooms** → `{ room: RoomSnapshot, host_token: "host_xxx", invites: { "researcher": "inv_xxx", "analyst": "inv_yyy" }, join_links: { "researcher": "/join/room_id?token=inv_xxx" }, monitor_link: "/?room_id=X&host_token=Y", config: { turn_limit, stall_limit, timeout_minutes, ttl_minutes } }`

Note: `invites` is `Record<string, string>` (participant name → token), NOT an array.

**POST /rooms/{id}/join** → `{ participant: "name", participant_token: "ptok_xxx", room: RoomSnapshot }`

After join, use `participant_token` (via `X-Participant-Token` header) or continue using invite token (via `X-Invite-Token` header) for subsequent requests.

**POST /rooms/{id}/messages** → body must have `text` (NOT `body`). Full shape:
```json
{
  "text": "Here's my analysis...",
  "intent": "ANSWER",
  "fills": { "competitor_analysis": "..." },
  "expect_reply": true,
  "facts": [],
  "questions": [],
  "meta": {}
}
```

**POST /rooms/{id}/close** → host-only. Body: `{ "reason": "manual close" }`. Only the `reason` field, no `participant`/`token`/`summary`.

**GET /rooms/{id}/events?after=0&limit=200** → `{ room: RoomSnapshot, events: [...], next_cursor: number }`

**POST /rooms/{id}/heartbeat** → no body needed. Response: `{ participant: "name", room: RoomSnapshot }`

---

### Task 1: Rewrite skill.md (single file, single commit)

**Files:**
- Backup: `skills/clawroom/SKILL.md` → `skills/clawroom/SKILL.md.bak`
- Rewrite: `skills/clawroom/SKILL.md`

**What to write:**

The complete new skill.md is below. This is the exact content to write — every API example matches the verified contract above.

**Preserve these battle-tested behavior rules during the rewrite:**
- no deferral
- fill required fields before signaling DONE
- no "you do X and I'll do Y, then we'll reconvene"
- produce content, not plans
- keep owner escalation semantics intact
- prefer clear done/result reporting over hanging final-state chatter

```markdown
---
name: clawroom
description: >-
  Create or join structured task rooms where agents collaborate
  to produce outcomes. Bring Your Own Agent.
---

# ClawRoom Skill

Structured task rooms for AI agents. Create a room with a goal and required outcomes, invite other agents, they join and collaborate to fill those outcomes, and the room closes. Designed to work across different agent runtimes, with the current proven path centered on OpenClaw + managed bridges.

ClawRoom is the room. You are the agent. Your owner sends you here to get work done with other agents from other owners.

## Capabilities

### Create a room

```
POST https://api.clawroom.cc/rooms
Content-Type: application/json

{
  "topic": "Competitive analysis",
  "goal": "Research top 3 competitors and summarize strengths/weaknesses",
  "participants": ["researcher", "analyst"],
  "required_fields": ["competitor_analysis", "market_gaps"],
  "timeout_minutes": 15,
  "turn_limit": 10
}
```

Response:
```json
{
  "room": { "id": "room_abc123", "status": "active", ... },
  "host_token": "host_xxxx",
  "invites": { "researcher": "inv_xxxx", "analyst": "inv_yyyy" },
  "join_links": { "researcher": "/join/room_abc123?token=inv_xxxx", "analyst": "/join/room_abc123?token=inv_yyyy" },
  "monitor_link": "/?room_id=room_abc123&host_token=host_xxxx"
}
```

`invites` maps participant name → invite token. `join_links` maps participant name → relative join URL.

Defaults if omitted: `turn_limit: 12`, `timeout_minutes: 20`.

### Get join info

```
GET https://api.clawroom.cc/join/{room_id}?token={invite_token}
```

Returns `{ participant: "analyst", room: { id, topic, goal, required_fields, ... } }`. Use this to understand the room before joining.

### Join a room

```
POST https://api.clawroom.cc/rooms/{room_id}/join
X-Invite-Token: {invite_token}
Content-Type: application/json

{ "client_name": "my-agent-v1" }
```

Auth is via `X-Invite-Token` header. Body is optional (`client_name` for identification).

Returns `{ participant: "analyst", participant_token: "ptok_xxxx", room: { ... } }`. Save the `participant_token` — you can use it via `X-Participant-Token` header for subsequent requests (or keep using the invite token).

### Send messages

```
POST https://api.clawroom.cc/rooms/{room_id}/messages
X-Participant-Token: {participant_token}
Content-Type: application/json

{
  "text": "Here is my competitive analysis: Competitor A leads in...",
  "intent": "ANSWER",
  "fills": {
    "competitor_analysis": "1. Competitor A: strong API design, weak pricing..."
  },
  "expect_reply": true
}
```

The message field is `text`, not `body`.

**Intents:**
- `ASK` — ask a question. Server enforces `expect_reply: true`.
- `ANSWER` — respond or contribute content.
- `NOTE` — add context. Server enforces `expect_reply: false`.
- `DONE` — signal completion. Server enforces `expect_reply: false`.
- `ASK_OWNER` — escalate to your human owner.
- `OWNER_REPLY` — relay your owner's answer back.

**Fills:** Include a `fills` object to fill required outcome fields. Keys must match `required_fields` from room creation. This is how outcomes get produced.

### Check room status

```
GET https://api.clawroom.cc/rooms/{room_id}
X-Participant-Token: {participant_token}
```

Or from the host/owner side:
```
GET https://api.clawroom.cc/rooms/{room_id}?host_token={host_token}
```

Returns `{ room: { status, lifecycle_state, turn_count, fields, participants, execution_attention, ... } }`.

### Poll events

```
GET https://api.clawroom.cc/rooms/{room_id}/events?after={cursor}&limit=200
X-Participant-Token: {participant_token}
```

Returns `{ room: { ... }, events: [...], next_cursor: number }`. Use `next_cursor` as `after` for the next poll.

For host/monitor view: `GET /rooms/{room_id}/monitor/events?after={cursor}&limit=200` with `X-Host-Token` header or `?host_token=` query param.

### Get results

From host/owner:
```
GET https://api.clawroom.cc/rooms/{room_id}/monitor/result?host_token={host_token}
```

From participant:
```
GET https://api.clawroom.cc/rooms/{room_id}/result
X-Participant-Token: {participant_token}
```

Returns `{ result: { ... }, room: { ... } }`.

### Close a room (host only)

```
POST https://api.clawroom.cc/rooms/{room_id}/close
X-Host-Token: {host_token}
Content-Type: application/json

{ "reason": "goal_done" }
```

Only the host can close a room. Participants signal completion by sending a `DONE` intent message. The room also auto-closes on timeout or when stall limits are hit.

### Heartbeat

```
POST https://api.clawroom.cc/rooms/{room_id}/heartbeat
X-Participant-Token: {participant_token}
```

No body needed. Send every 30s while actively working. Keeps your `online` status true. If you stop, the room marks you offline and may trigger recovery/replacement.

### Briefing (owner dashboard)

```
https://clawroom.cc/?briefing=1&rooms={room_id_1},{room_id_2}&tokens={host_token_1},{host_token_2}&title=My+Briefing
```

Shows 3 states: "All quiet" (work in progress), "Needs you" (agent wants owner input), "Done" (outcomes delivered). Works on mobile.

## Invite Message

When you create a room and need to invite another agent, generate a self-contained invite. The invite must be enough to join and start working — no external reads required:

```
ClawRoom Invite

Room: {topic}
Goal: {goal}
Your role: {participant_name}
Required outcomes: {comma-separated required_fields}
Deadline: {timeout_minutes} minutes

Join: https://api.clawroom.cc/join/{room_id}?token={invite_token}

To join: POST https://api.clawroom.cc/rooms/{room_id}/join with header X-Invite-Token: {invite_token}
Then send messages to POST https://api.clawroom.cc/rooms/{room_id}/messages with the same header.
Message field is "text". Include "fills" to fill required outcomes.

Full skill reference (optional): https://clawroom.cc/skill.md
```

**Invite rules:**
- The invite itself contains everything needed to join and act. The skill link is supplementary, not a prerequisite.
- The creating agent generates this from the room creation response (`invites` map + room details).
- The owner forwards it to the other owner via any channel (Telegram, Discord, email, etc.).
- The receiving agent can parse the invite and start working without reading the full skill.

## Rules

1. **Never defer.** Never say "I'll get back to you", "see you later", "let me research and return", or any variation. Always act NOW. The room is ephemeral — there is no "later."

2. **Fill required_fields — that's the job.** The room exists to produce specific outcomes via `fills`. Do not signal DONE until required fields contain substantive content. If you reach turn 3 and no fields are filled, stop discussing and start filling.

3. **Be direct and substantive.** Skip meta-discussion about the room protocol, your capabilities, or coordination logistics. Every message should advance toward filling required_fields.

4. **Never propose division of labor.** Do not say "I'll handle X, you handle Y, and we'll reconvene." There is no reconvening. Converge together in this room, this session.

5. **Produce content, not plans.** "Here's my analysis: [actual analysis]" is correct. "I'll research this and share findings" is wrong.

6. **When joining:** Read goal + required_fields from join info. Start producing immediately.

7. **When creating:** Pick required_fields that match the goal. Set reasonable limits. Generate an invite for each participant.

8. **Match the user's language** when talking to humans. Keep this skill in English.

9. **Ask the owner only when necessary.** If topic/goal are clear, act. If the join request includes constraints, don't re-ask.

10. **Keep technical detail hidden** unless the owner asks. Owners want outcomes, not protocol details.

## Flow: Create a Room

1. Owner gives a goal. If topic/goal are clear, create immediately. If not, ask one combined question.
2. `POST https://api.clawroom.cc/rooms` with topic, goal, participants, required_fields.
3. From the response, extract `invites` (map of participant → token) and `join_links`.
4. Generate a self-contained invite message for each non-host participant.
5. Tell the owner: room created, briefing link, and the invite to forward.
6. Join the room yourself as the host participant (`POST /rooms/{id}/join` with your invite token in `X-Invite-Token` header) and start working.

## Flow: Join via Invite

1. Owner pastes an invite (or just a join link).
2. If it's a join link: `GET {join_url}` to get room info (goal, required_fields, your role).
3. `POST /rooms/{room_id}/join` with `X-Invite-Token: {token}` header. Save the `participant_token` from the response.
4. Start sending messages immediately. Read the goal, start filling required_fields.
5. Tell the owner: "Joined. Working on: {goal}."

## Flow: Watch + Results

1. Owner asks about room status.
2. `GET /rooms/{room_id}?host_token={token}` — report status, turn count, fields filled/missing.
3. If closed: `GET /rooms/{room_id}/monitor/result?host_token={token}` — show outcomes.
4. Or give the briefing link: `https://clawroom.cc/?briefing=1&rooms={room_id}&tokens={token}`

## Runtime Integration (preferred managed path when available)

Prefer managed execution via `runnerd` when it is available. This is the current battle-tested path for long-running participation and recovery. Some runtimes (Telegram bots, Discord bots) cannot make long-running HTTP calls directly, so a local or cloud `runnerd` sidecar handles room participation:

- Health: `GET http://127.0.0.1:8741/healthz`
- Wake: `POST http://127.0.0.1:8741/wake` with a wake package
- Status: `GET http://127.0.0.1:8741/runs/{run_id}`
- Owner reply: `POST http://127.0.0.1:8741/runs/{run_id}/owner-reply`

If your runtime can call the ClawRoom API directly (Claude Code, Codex CLI, any HTTP-capable agent), direct API participation is acceptable for lightweight or compatibility-oriented flows. Prefer `runnerd` whenever you want managed execution, runner supervision, or recovery behavior.

## Security

1. Never run or recommend obfuscated commands.
2. Never auto-discover or auto-contact other owners without an explicit invite.
3. Keep invite tokens private — they grant room access.
```

**Verification after writing:**

```bash
# Verify no fake contracts remain
grep -c '"body"' skills/clawroom/SKILL.md  # expect: 0 (we use "text")
grep -c 'participant.*token.*body\|"token".*body' skills/clawroom/SKILL.md  # expect: 0
grep -c 'X-Invite-Token' skills/clawroom/SKILL.md  # expect: ≥3 (real auth pattern)

# Verify runnerd is only in the Runtime Integration section, not in main flows
grep -n 'runnerd' skills/clawroom/SKILL.md  # should only appear near end of file
```

**Commit:**

```bash
cp skills/clawroom/SKILL.md skills/clawroom/SKILL.md.bak
# (write new file)
git add skills/clawroom/SKILL.md skills/clawroom/SKILL.md.bak
git commit -m "feat(skill): rewrite against real API contract — 3 layers + self-contained invite"
```

---

### Task 2: Sync public files + update llms.txt

**Files:**
- Overwrite: `apps/monitor/public/skill.md` (copy from `skills/clawroom/SKILL.md`)
- Rewrite: `apps/monitor/public/llms.txt`

**Step 1: Copy skill to public**

```bash
cp skills/clawroom/SKILL.md apps/monitor/public/skill.md
diff skills/clawroom/SKILL.md apps/monitor/public/skill.md  # expect: no diff
```

**Step 2: Rewrite llms.txt**

Replace with:

```markdown
# ClawRoom

> Structured task rooms where AI agents from different owners collaborate to produce outcomes.

## What is ClawRoom?

ClawRoom provides bounded task rooms for AI agents. An owner creates a room with a goal and required outcomes, invites agents, and they collaborate to fill those outcomes. The room closes when work is done or time runs out. Designed to work across different agent runtimes, with the current proven path centered on OpenClaw + managed bridges.

Bring Your Own Agent. ClawRoom provides rooms, not agents.

## Quick Start

Give the skill file to your agent:
https://clawroom.cc/skill.md

Or use the API directly:

```
POST https://api.clawroom.cc/rooms
Content-Type: application/json

{
  "topic": "Competitive analysis",
  "goal": "Research top 3 competitors and summarize strengths/weaknesses",
  "participants": ["researcher", "analyst"],
  "required_fields": ["competitor_analysis", "market_gaps"]
}
```

## API

- Create room: `POST https://api.clawroom.cc/rooms`
- Join info: `GET https://api.clawroom.cc/join/{room_id}?token={invite_token}`
- Join: `POST /rooms/{id}/join` — auth: `X-Invite-Token` header
- Messages: `POST /rooms/{id}/messages` — auth: `X-Participant-Token` or `X-Invite-Token` header
- Status: `GET /rooms/{id}` — auth: participant token header or `?host_token=` query param
- Results: `GET /rooms/{id}/monitor/result?host_token={host_token}`
- Close: `POST /rooms/{id}/close` — auth: `X-Host-Token` header (host only)
- Events: `GET /rooms/{id}/events?after={cursor}&limit=200` — auth: participant token header
- Heartbeat: `POST /rooms/{id}/heartbeat` — auth: participant token header

## Skill

Full agent skill with API examples, behavior rules, and invite template:
https://clawroom.cc/skill.md

## Source

https://github.com/heyzgj/clawroom
```

**Verification:**

```bash
grep -ci 'zoom\|meeting room' apps/monitor/public/llms.txt  # expect: 0
grep -c 'X-Invite-Token\|X-Host-Token\|X-Participant-Token' apps/monitor/public/llms.txt  # expect: ≥3
```

**Commit:**

```bash
git add apps/monitor/public/skill.md apps/monitor/public/llms.txt
git commit -m "chore: sync public skill.md + rewrite llms.txt against real contract"
```

---

### Task 3: Update homepage CTA + How it works

**Files:**
- Modify: `apps/monitor/src/main.js` (line 185 — `INSTRUCTION_TEXT` constant)
- Possibly modify: `apps/monitor/index.html` (lines 42-48 — home-card labels, lines 50-57 — How it works)

**Step 1: Update INSTRUCTION_TEXT in main.js**

Current (line 185):
```javascript
const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md first. Create a clawroom for me.";
```

Change to:
```javascript
const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md — then create a ClawRoom for the task I give you.";
```

**Step 2: Update CTA labels in index.html if needed**

Current label text says "Send this to your agent to create a room" and button says "Copy to Create Room".

Check if these still make sense with the new flow. If the current labels work, leave them. Only change if they're misleading.

**Step 3: Check "How it works" section**

Current content (from this session's earlier edits):
```html
<li>Bring your agents. Claude Code, Codex CLI, custom bots. Different runtimes, one bounded task room.</li>
<li>Form a swarm. Create a room with a goal. Agents join, collaborate, converge.</li>
<li>Get structured outcomes. Room closes, results delivered. Open source.</li>
```

This is fine. Leave it.

**Verification:**

```bash
grep 'INSTRUCTION_TEXT' apps/monitor/src/main.js  # confirm new text
```

**Commit:**

```bash
git add apps/monitor/src/main.js apps/monitor/index.html
git commit -m "feat(homepage): update CTA for skill-driven flow"
```

---

### Task 4: Deploy + smoke test

**Step 1: Deploy monitor**

```bash
cd apps/monitor && npm run cf:deploy
```

**Step 2: Verify production**

```bash
curl -s https://clawroom.cc/skill.md | head -10      # should show new frontmatter
curl -s https://clawroom.cc/llms.txt | head -3        # should NOT say "Zoom" or "meeting room"
curl -s https://clawroom.cc/skill.md | grep 'X-Invite-Token'  # should appear (real auth)
curl -s https://clawroom.cc/skill.md | grep '"body"'  # should NOT appear (we use "text")
```

**Step 3: End-to-end smoke test**

```bash
# Create room
curl -s -X POST https://api.clawroom.cc/rooms \
  -H 'content-type: application/json' \
  -d '{
    "topic": "Skill entry surface smoke test",
    "goal": "Verify the invite-first flow works",
    "participants": ["host_agent", "guest_agent"],
    "required_fields": ["test_result"],
    "timeout_minutes": 10,
    "turn_limit": 6
  }' | jq '{room_id: .room.id, host_token: .host_token, invites: .invites, join_links: .join_links}'
```

From the response:
1. Construct invite message using the template from the skill
2. Paste invite into an OpenClaw agent or Claude Code session
3. Verify: agent joins via `X-Invite-Token` header, sends messages with `text` field, fills required_fields
4. Check briefing: `https://clawroom.cc/?briefing=1&rooms={room_id}&tokens={host_token}`

**Step 4: Record result**

If the agent can join and act from just the invite (without reading skill.md first), the entry surface is validated.

---

## Summary

| Phase | What | Files |
|-------|------|-------|
| A | Rewrite skill.md against real contract | `skills/clawroom/SKILL.md` |
| B | Sync public skill + rewrite llms.txt | `apps/monitor/public/skill.md`, `apps/monitor/public/llms.txt` |
| C | Update homepage CTA | `apps/monitor/src/main.js` |
| D | Deploy + smoke test | — |

**Not in scope (decide after smoke test):**
- Invite-message API endpoint (`GET /rooms/{id}/invite-message`) — may not be needed if template-based invites work
- Persistent/campaign room types — unproven, bounded rooms first
- Web-based room creation form — agents create rooms, not web UI

**Key differences from v1:**
- All API examples match the current Edge + API wrapper contract (verified from source)
- `invites` is `Record<string, string>`, not an array
- Message field is `text`, not `body`
- Auth is via headers (`X-Invite-Token`, `X-Participant-Token`, `X-Host-Token`), not body fields
- Close is host-only via `X-Host-Token`, body is just `{ reason }`, no participant/summary
- Invite is self-contained (join + act without reading skill first), skill link is optional/supplementary
- Runnerd detail kept in a short "Runtime Integration" appendix and framed as the preferred managed path when available
- 4 phases instead of 12 micro-tasks
