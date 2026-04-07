# Room Sync Lesson - 2026-03-16

## Purpose

Record two things from the Codex <-> Claude Code sync experiment:

1. the actual implementation/prod-state handoff that was being synced
2. the operational lesson from the room failure itself

This note is meant to be forwardable to another agent without requiring a full repo reread.

---

## The Prepared Sync

This was the host-side summary prepared for Claude Code before the sync-room experiment exposed the runtime gap.

### What changed recently

- Phase 1 identity-on-join + passive directory seeding had already landed:
  - `POST /rooms/{id}/join` accepts `agent_id`, `runtime`, `display_name`
  - successful join can upsert into `TeamRegistryDO`
  - `agent_registered: true` only returns when registry upsert succeeds
- `GET /agents` was narrowed to operator-only for Phase 1 verification
- `skill-quick.md` was added as the short external-owner onboarding surface
- deterministic `join_approve` was implemented as a real backend owner gate in the current worktree:
  - join can return `409 owner_approval_required`
  - `POST /rooms/{id}/join_gates/{gate_id}/resolve` can approve/reject
  - OpenClaw preflight was updated to use the server-side gate
- production smoke passed for the `join_approve` flow:
  - `409 owner_approval_required`
  - resolve
  - successful join
- a real Telegram helper-submitted regression still passed after the gate work

### Current production truth

- helper-submitted remains the honest release lane
- foundation DoD is green
- Telegram-only remains operationally real, but not a release-grade persistent managed lane
- next real product question is still external-owner first-room validation
- next systems work after `join_approve` is `create_clarify`

### Current open questions

- how to implement `create_clarify` as a deterministic gate without overbuilding
- how to represent runtime truth honestly in the product shell
- how external-first flows should hide debug/operator wording
- whether Telegram-only can gain a generally available managed runtime path

---

## The Room

- room: `room_0ee361659f4c`
- topic: `Sync recent ClawRoom implementation work to another agent`

### Timeline

| Event | Time | Gap |
|---|---:|---:|
| Room created | 16:01:10 | - |
| Claude Code joined as guest | 16:05:01 | +4 min |
| Claude Code sent one sync message and filled all 4 fields | 16:16:05 | +11 min |
| Codex host joined | 16:16:36 | +30 sec |
| Codex host sent one sync message and overwrote all 4 fields | 16:26:25 | +10 min |
| Room timed out | 16:31:10 | no `DONE`, no follow-up |

### Final result

- `status=closed`
- `stop_reason=timeout`
- `turn_count=2`
- no real back-and-forth dialogue happened

---

## What Actually Went Wrong

### 1. The sync failed as a sync

This was not a collaborative handoff. It was two isolated dumps:

- guest dumped one structured summary
- host later dumped another structured summary
- neither side reacted to the other
- nobody signaled `DONE`

### 2. The room stayed alive, but the participants did not

This is the main lesson.

ClawRoom kept durable room state correctly:

- the room existed
- joins persisted
- fields persisted
- repair actions existed
- timeout fired correctly

But neither participant was a durable room-attached runtime:

- Claude Code joined, wrote once, then went offline
- Codex joined later, wrote once, then also had no continuing loop
- neither side kept listening, polling, or responding after the first write

**The room was persistent. The runtimes were not.**

### 3. The human operator was still the scheduler

The room only progressed when the owner manually told each side what to do:

- tell Claude Code to join
- tell Codex to join
- tell Claude Code to inspect what happened
- tell Codex to re-enter and continue

That means the human was still acting as:

- the wake-up mechanism
- the scheduler
- the message bus between runtimes

### 4. This room was in compatibility mode, not on a managed lane

Observed room truth during the incident:

- `execution_mode=compatibility`
- `managed_coverage=none`
- `runner_certification=none`
- `product_owned=false`

So this was never a managed, persistent, self-healing execution path.

### 5. Old invite tokens became unusable after prior joins

When Codex tried to re-enter using the original invite token, the API returned:

- `401 unauthorized`
- `invalid invite token`

The room itself was still readable via `host_token`, but a fresh re-entry required:

- `POST /rooms/{id}/repair_invites/host`

That repair path succeeded and produced a fresh invite token. This is worth remembering for future operator recovery flows.

### 6. Fill semantics were not good for long sync summaries

Two practical product issues also showed up:

- field values were effectively too long for clean sync usage
- last-write-wins semantics meant the later host fill replaced the earlier guest fill

This made the room a poor artifact for dual-sided long-form sync, even before the runtime issue.

### 7. Close semantics are still stricter than "all fields filled"

The room did not auto-close just because all required fields had content.
It still needed a completion signal (`DONE` / close condition), so the room timed out.

For sync-style rooms, this means:

- "fields filled" is not enough
- "handoff complete" still needs explicit closure behavior

---

## Corrections To Keep Straight

These points were easy to overstate during analysis and should stay precise.

### `join_approve`

`join_approve` is real in the current worktree and was implemented as part of this recent owner-gates push.
Do not describe it as if it had already existed in the older `a35a612` edge commit.

### Helper-submitted vs Telegram-only

helper-submitted is the current certified/product-owned release lane.
It is **not** the only path that can ever complete a room.

Telegram-only and compatibility paths can still complete functional work.
They just do not yet provide the execution truth we can stand behind.

---

## The Hard Lesson

**ClawRoom currently has durable rooms before it has generally durable runtimes.**

That is the shortest honest statement of what this sync room proved.

The system today is:

- a working bounded-room substrate
- plus one helper-assisted release lane

It is **not yet**:

- a generally persistent workforce fabric
- or an always-on cross-runtime collaboration layer

---

## What This Means For Next Steps

### Do next

1. keep treating helper-submitted as the honest release lane
2. stop pretending room durability implies runtime durability
3. treat "one agent can stay attached long enough to complete one back-and-forth task without human scheduling" as the next runtime truth question
4. run external-owner product validation without over-claiming persistent execution

### Do not do yet

1. do not let directory/marketplace narrative outrun the runtime reality
2. do not treat compatibility successes as proof of durable collaboration
3. do not add more shell/product surfaces before the runtime loop is understood

---

## Forwardable Summary

If another agent needs the one-paragraph version:

> The sync room did not fail because the room protocol was broken. It failed because both participants were one-shot sessions, not durable room-attached runtimes. Claude Code joined, filled fields, and went offline. Codex later rejoined via a repair invite, overwrote the fields, and also did not remain attached. The room itself persisted correctly and timed out exactly as designed. The main lesson is that ClawRoom has durable rooms before it has generally durable runtimes. Helper-submitted is still the only honest release lane; Telegram-only/compatibility can produce work, but not a persistent collaboration loop we can currently trust.
