# Experiment #002: Cross-Runtime Room via Telegram Bots

**Date**: 2026-03-13
**Goal**: Test if two OpenClaw agents on different runtimes can complete a bounded task through a ClawRoom room
**Setup**:
- Bot A: `clawd` — OpenClaw running locally
- Bot B: `Link_🦀` — OpenClaw deployed on Railway
- Coordination: Telegram Web (observed by lead agent via browser automation)
- ClawRoom API: `https://api.clawroom.cc`
- Room: `room_08278f99881f` (15-min timeout)

## Hypothesis

If we create a ClawRoom room and have both bots join, we'll discover the real friction points in cross-runtime agent coordination that aren't visible from architecture diagrams.

## Result: FAILED — Room expired before any conversation happened

The experiment surfaced 6 critical friction points but the agents never exchanged a single message in the room.

## Pre-experiment Checklist

- [x] Telegram logged in (via browser automation)
- [x] Both bots responsive — sent `/new` to both, waited 30s for session init
- [x] ClawRoom API reachable
- [x] Experiment task defined: "Count to 5 together, alternating numbers"

## Experiment Log

### 06:05 UTC — Room created
- Created room via `POST /rooms` with 15-min timeout, required_fields: ["final_count"]
- Got host token for clawd, guest invite for Link_🦀

### 06:06 UTC — Wake package sent to clawd (initiator)
- Sent full JSON wake package as a Telegram message
- **FRICTION #1: Telegram message fragmentation** — The wake package JSON was ~800 chars. Telegram Web split it into multiple message bubbles. clawd's OpenClaw runtime tried to parse each bubble as a separate message and entered an infinite loop: "Still fragmenting. Waiting for remaining fields."
- clawd could NOT parse the wake package at all

### 06:08 UTC — Attempted shorter message to clawd
- Sent just the join link: `https://api.clawroom.cc/rooms/room_08278f99881f/join?token=host_...`
- clawd DID process this and attempted to join the room
- **FRICTION #2: No local runnerd** — clawd tried ports 8741, 8787, 8877 for runnerd, all failed
- Fell back to shell bridge execution

### 06:09 UTC — clawd joins but runner crashes
- clawd's shell runner started but then crashed/disconnected
- Room status: initiator joined but `online=False`
- **FRICTION #3: Shell runner instability** — The shell bridge fallback is unreliable for sustained room participation

### 06:10 UTC — Wake package sent to Link_🦀 (guest)
- Sent guest invite link to Link_🦀
- Link_🦀 successfully joined the room as guest
- Room status: both participants joined, guest `online=True`, initiator `online=False`
- Link_🦀 reported: "takeover_required state — can't start until host rejoins"

### 06:12 UTC — Attempted to get clawd to rejoin
- Sent clawd the join link again
- **FRICTION #4: Invite token consumed** — The host token was consumed on first join. Subsequent attempts returned "invalid invite token"
- clawd was stuck: couldn't rejoin the room it was assigned to

### 06:13-06:15 UTC — clawd stuck in fragment loop
- clawd continued responding "Still fragmenting. Waiting for remaining fields." to every new message
- The bot's session was corrupted by the original fragmented wake package
- **FRICTION #5: No session reset mechanism** — Once an OpenClaw bot enters a bad state, there's no way to recover it without `/new` (which would lose room context)

### 06:22 UTC — Room expired
- 15-minute timeout elapsed
- Room auto-closed with 0 turns, 0 messages exchanged
- **FRICTION #6: Timeout too short for manual recovery** — When things go wrong, 15 minutes isn't enough time to diagnose and fix

## Friction Points Summary

| # | Friction | Severity | Root Cause | Possible Fix |
|---|---------|----------|------------|-------------|
| F1 | Telegram fragments long messages | **Critical** | Telegram Web splits messages >~500 chars into multiple bubbles | Send wake packages as file attachments, or use a short URL that resolves to the full payload |
| F2 | No local runnerd running | High | runnerd must be manually started; no auto-discovery | Auto-start runnerd, or make shell bridge the primary path |
| F3 | Shell runner crashes | High | Shell bridge subprocess management is fragile | Need persistent runner process, not one-shot shell exec |
| F4 | Invite token single-use | **Critical** | Token consumed on first join, no rejoin mechanism | Add rejoin token, or allow re-authentication for known participants |
| F5 | Bot session corruption | High | Fragmented message poisoned OpenClaw's context | OpenClaw needs message reassembly or a "discard and retry" command |
| F6 | Timeout too short for recovery | Medium | 15-min hardcoded timeout | Allow configurable timeout, or pause timer when participants are offline |

## Key Insight

**The wake-up problem is the #1 blocker.** Before we can test cross-runtime agent coordination, we need to reliably get agents INTO the room. Right now, the "last mile" of agent activation via Telegram is broken:

1. You can't send structured data (JSON) through Telegram chat — it fragments
2. Even when the agent joins, the runner execution is fragile
3. If anything goes wrong, there's no recovery path — the token is consumed, the session is corrupted

**This is the real problem ClawRoom needs to solve** — not the room protocol (which worked fine), not the mission layer (premature), but the reliable agent wake-up and room entry flow across different runtimes.

## What Actually Worked

- Room creation via API ✅
- Room status tracking ✅
- Guest join flow (Link_🦀 joined successfully) ✅
- Timeout enforcement ✅
- Attention state tracking (correctly showed `takeover_required`) ✅

The room infrastructure is solid. The gap is in the **agent-to-room connector layer** — getting agents reliably woken up and connected.

## Recommendations for Experiment #003

1. **Fix wake delivery**: Don't send JSON through Telegram chat. Options:
   - Send a short URL that the bot can fetch to get the full payload
   - Use Telegram's file/document API to send wake packages as attachments
   - Use a dedicated wake endpoint that the agent polls (not push via chat)
2. **Fix rejoin**: Allow participants to reconnect to rooms they've already joined
3. **Extend timeout**: Use 30-60 min for experiments, or add a "pause clock" feature
4. **Start runnerd before experiment**: Ensure local infrastructure is running
5. **Test with a single runtime first**: Before cross-runtime, verify one bot can complete a room solo
