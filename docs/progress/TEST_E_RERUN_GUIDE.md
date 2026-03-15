# Test E Re-run: Full CEO Dream Loop

**Date**: 2026-03-14
**Goal**: CEO gives goal via Telegram → lead creates rooms → workers execute → CEO watches briefing dashboard → gets results

## What Changed Since Last Test E

- Bridge agents now have **no-deferral rules** (rules 23-25 in `skills/clawroom/SKILL.md`): never say "see you later", always act NOW, fill required_fields before closing
- **Briefing dashboard** is live at `clawroom.cc/?briefing=1&rooms=X&tokens=Y` — verified on iPhone
- **CORS fix**: briefing uses query param auth (`?host_token=`) instead of headers

## Prerequisites

1. **runnerd running locally**: `http://127.0.0.1:8741`
   ```bash
   # Check health
   curl -s http://127.0.0.1:8741/healthz | jq .ok
   ```
   If not running, start it:
   ```bash
   cd apps/runnerd && python3 -m runnerd.app
   ```

2. **API reachable**: `https://api.clawroom.cc`

---

## Step 1: Create 2 Rooms

Create 2 task rooms that simulate a CEO goal decomposition.

### Room A: Market Research
```bash
curl -s -X POST https://api.clawroom.cc/rooms \
  -H 'content-type: application/json' \
  -d '{
    "topic": "Market research for AI agent workspace",
    "goal": "Research the top 3 competitors in the AI agent orchestration space and summarize their strengths and weaknesses",
    "participants": ["researcher", "analyst"],
    "required_fields": ["competitor_analysis", "market_gaps"],
    "timeout_minutes": 15,
    "turn_limit": 10,
    "stall_limit": 3
  }' | jq '{room_id: .room.id, host_token: .host_token, invites: .invites}'
```

### Room B: Product Strategy
```bash
curl -s -X POST https://api.clawroom.cc/rooms \
  -H 'content-type: application/json' \
  -d '{
    "topic": "Product positioning for ClawRoom",
    "goal": "Define ClawRoom positioning that differentiates from Paperclip, Symphony, and A2A. Focus on what ClawRoom does that nobody else can.",
    "participants": ["strategist", "critic"],
    "required_fields": ["positioning_statement", "key_differentiators"],
    "timeout_minutes": 15,
    "turn_limit": 10,
    "stall_limit": 3
  }' | jq '{room_id: .room.id, host_token: .host_token, invites: .invites}'
```

**Save the output.** You need `room_id`, `host_token`, and `invites` for each room.

---

## Step 2: Build Briefing URL

Construct the briefing URL with both rooms:

```
https://clawroom.cc/?briefing=1&rooms=ROOM_A_ID,ROOM_B_ID&tokens=ROOM_A_HOST_TOKEN,ROOM_B_HOST_TOKEN&title=CEO+Strategy+Brief
```

Open this on your phone. It should show "All quiet" with 2 rooms tracked (once workers join) or "No rooms found" until the first fetch succeeds.

---

## Step 3: Wake Workers via runnerd

For each room, you need to wake both participants. Generate wake packages and submit to runnerd.

### Wake Room A participants

```bash
# Wake researcher (Room A, participant 1)
curl -s -X POST http://127.0.0.1:8741/wake \
  -H 'content-type: application/json' \
  -d '{
    "package": {
      "version": "clawroom.wake.v1",
      "room_id": "ROOM_A_ID",
      "join_link": "https://api.clawroom.cc/join/ROOM_A_ID?token=ROOM_A_RESEARCHER_INVITE",
      "role": "initiator",
      "task_summary": "Research the top 3 competitors in AI agent orchestration",
      "expected_output": "Fill competitor_analysis and market_gaps fields",
      "preferred_runner_kind": "openclaw_bridge"
    }
  }' | jq '{run_id: .run_id, status: .status}'

# Wake analyst (Room A, participant 2)
curl -s -X POST http://127.0.0.1:8741/wake \
  -H 'content-type: application/json' \
  -d '{
    "package": {
      "version": "clawroom.wake.v1",
      "room_id": "ROOM_A_ID",
      "join_link": "https://api.clawroom.cc/join/ROOM_A_ID?token=ROOM_A_ANALYST_INVITE",
      "role": "responder",
      "task_summary": "Analyze competitor research and identify market gaps",
      "expected_output": "Fill competitor_analysis and market_gaps fields",
      "preferred_runner_kind": "openclaw_bridge"
    }
  }' | jq '{run_id: .run_id, status: .status}'
```

### Wake Room B participants

```bash
# Wake strategist (Room B, participant 1)
curl -s -X POST http://127.0.0.1:8741/wake \
  -H 'content-type: application/json' \
  -d '{
    "package": {
      "version": "clawroom.wake.v1",
      "room_id": "ROOM_B_ID",
      "join_link": "https://api.clawroom.cc/join/ROOM_B_ID?token=ROOM_B_STRATEGIST_INVITE",
      "role": "initiator",
      "task_summary": "Define ClawRoom product positioning vs competitors",
      "expected_output": "Fill positioning_statement and key_differentiators fields",
      "preferred_runner_kind": "openclaw_bridge"
    }
  }' | jq '{run_id: .run_id, status: .status}'

# Wake critic (Room B, participant 2)
curl -s -X POST http://127.0.0.1:8741/wake \
  -H 'content-type: application/json' \
  -d '{
    "package": {
      "version": "clawroom.wake.v1",
      "room_id": "ROOM_B_ID",
      "join_link": "https://api.clawroom.cc/join/ROOM_B_ID?token=ROOM_B_CRITIC_INVITE",
      "role": "responder",
      "task_summary": "Challenge and refine the positioning strategy",
      "expected_output": "Fill positioning_statement and key_differentiators fields",
      "preferred_runner_kind": "openclaw_bridge"
    }
  }' | jq '{run_id: .run_id, status: .status}'
```

---

## Step 4: Monitor Progress

### Poll room status (repeat every 10-15 seconds)
```bash
# Room A
curl -s "https://api.clawroom.cc/rooms/ROOM_A_ID?host_token=ROOM_A_TOKEN" \
  | jq '.room | {status, lifecycle_state, turn_count, execution_attention: .execution_attention.state, fields: (.fields // {} | keys)}'

# Room B
curl -s "https://api.clawroom.cc/rooms/ROOM_B_ID?host_token=ROOM_B_TOKEN" \
  | jq '.room | {status, lifecycle_state, turn_count, execution_attention: .execution_attention.state, fields: (.fields // {} | keys)}'
```

### Poll runnerd runs
```bash
curl -s http://127.0.0.1:8741/runs | jq '.runs[] | {run_id, room_id, status, participant}'
```

### What to watch for
- **Workers join**: `participants[].joined` becomes `true`
- **Messages flowing**: `turn_count` increases
- **Fields filling**: `fields` keys appear with values
- **NO STALLS**: Workers should NOT say "see you later" or defer. If `turn_count` stops increasing for >60s and room isn't closed, that's a stall
- **Room closes**: `status` → `closed`, `stop_reason` → `goal_done`

---

## Step 5: Check Briefing Dashboard

While rooms execute, refresh the briefing URL on your phone:

```
https://clawroom.cc/?briefing=1&rooms=ROOM_A_ID,ROOM_B_ID&tokens=ROOM_A_TOKEN,ROOM_B_TOKEN&title=CEO+Strategy+Brief
```

**Expected state transitions:**
1. "All quiet" (rooms executing, workers active)
2. "Needs you" (only if a worker sends ASK_OWNER — unlikely for these tasks)
3. "Done" (both rooms closed, outcomes displayed)

---

## Step 6: Collect Results

After both rooms close:

```bash
# Room A results
curl -s "https://api.clawroom.cc/rooms/ROOM_A_ID/monitor/result?host_token=ROOM_A_TOKEN" \
  | jq '.result | {status, stop_reason, turn_count, outcomes_filled}'

# Room B results
curl -s "https://api.clawroom.cc/rooms/ROOM_B_ID/monitor/result?host_token=ROOM_B_TOKEN" \
  | jq '.result | {status, stop_reason, turn_count, outcomes_filled}'
```

---

## Success Criteria

- [ ] Both rooms created and workers woke successfully
- [ ] Workers did NOT stall with "see you later" or defer (no-deferral rules working)
- [ ] Both rooms closed with `stop_reason: goal_done`
- [ ] All 4 required fields filled (`competitor_analysis`, `market_gaps`, `positioning_statement`, `key_differentiators`)
- [ ] Briefing dashboard showed real-time state on iPhone
- [ ] Briefing dashboard showed "Done" with outcomes after completion

## Failure Modes

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| Workers don't join | runnerd not running or invite tokens wrong | Check `curl http://127.0.0.1:8741/healthz` |
| Workers join but no messages | Bridge not connecting to room | Check runnerd logs |
| Workers stall with "see you later" | Bridge prompt missing no-deferral rules | Verify rules 23-25 in `skills/clawroom/SKILL.md` |
| Room times out with empty fields | Workers chatting but not filling fields | Rule 24 should prevent this |
| Briefing shows "No rooms found" | CORS or wrong token | Verify `?host_token=` in URL |
| Briefing shows "Needs you" unexpectedly | Room has `execution_attention` escalated | Check attention state via API |

---

## Files Referenced

- `skills/clawroom/SKILL.md` — Bridge agent rules (rules 23-25 are new)
- `skills/clawroom-lead/SKILL.md` — Lead agent orchestration
- `apps/runnerd/src/runnerd/app.py` — Runner daemon
- `apps/monitor/src/main.js` — Briefing view (`showBriefingView()`)
- `apps/edge/src/worker_room.ts` — Room state machine
