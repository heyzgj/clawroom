# ClawRoom Handoff Context

Date: 2026-03-28  
Audience: another agent who needs to quickly understand current status, what has been validated, and what is still broken.

## 1. Executive Summary

ClawRoom now has a clear **golden path** that works in controlled environments:

- installable ClawRoom skill
- OpenClaw runtime
- `runnerd`
- `openclaw_bridge`
- managed room execution

That path has already been validated repeatedly in local managed E2E and produces:

- `execution_mode=managed_attached`
- `managed_coverage=full`
- `status=closed`
- owner-ready artifacts

However, the **real fresh OpenClaw user journey is still not release-grade**.

Main reason:

- the current skill is too broad and too long
- OpenClaw create/join/watch logic is mixed with many compatibility branches
- OpenClaw fresh sessions still fall into fallback behavior or partial/manual room participation

Current practical state:

- **artifact quality in controlled evals:** good
- **managed local/runtime path:** good
- **fresh OpenClaw chat-surface product flow:** not yet good enough

## 2. Project Shape

Main code areas:

- Edge backend: [/Users/supergeorge/Desktop/project/agent-chat/apps/edge](/Users/supergeorge/Desktop/project/agent-chat/apps/edge)
- Managed runner daemon: [/Users/supergeorge/Desktop/project/agent-chat/apps/runnerd](/Users/supergeorge/Desktop/project/agent-chat/apps/runnerd)
- OpenClaw bridge: [/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge](/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge)
- Shared client/runtime prompting: [/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core](/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core)
- Shared models: [/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core](/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core)
- Product skill source of truth: [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md)

Published installable skill repo:

- [https://github.com/heyzgj/clawroom](https://github.com/heyzgj/clawroom)

That repo has already been slimmed to a minimal skill bundle and is intended to be the main install target:

```bash
npx skills add heyzgj/clawroom
```

## 3. What Has Been Successfully Achieved

### 3.1 Managed golden path is real

Repeated runnerd bridge E2E has been green. Best reference:

- [/Users/supergeorge/Desktop/project/agent-chat/docs/progress/RUNNERD_E2E_LOG.md](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/RUNNERD_E2E_LOG.md)

These runs repeatedly show:

- `execution_mode=managed_attached`
- `runner_certification=certified`
- `product_owned=true`
- `automatic_recovery_eligible=true`
- rooms closing successfully

This is the most important "ground truth success" in the project.

### 3.2 Outcome/artifact quality improved materially

A lot of work has already been done on:

- field principles
- scenario-specific quality guidance
- post-close evaluation
- owner-ready packets instead of just `required_fields` completion

Key files:

- [/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core/evaluation.py](/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core/evaluation.py)
- [/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core/prompting.py](/Users/supergeorge/Desktop/project/agent-chat/packages/client/src/clawroom_client_core/prompting.py)
- [/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core/models.py](/Users/supergeorge/Desktop/project/agent-chat/packages/core/src/roombridge_core/models.py)

Controlled dry runs for friend-style business scenarios eventually reached `usable=true`:

- cross-role alignment
- founder persona sync
- task / OKR sync

Meaning: packet quality is no longer the primary blocker.

### 3.3 Skill packaging and publishing path now exist

There is now:

- a single source-of-truth skill
- a bundle exporter
- an installable repo
- a runtime preflight script

Key files:

- [/Users/supergeorge/Desktop/project/agent-chat/scripts/export_clawroom_skill_bundle.sh](/Users/supergeorge/Desktop/project/agent-chat/scripts/export_clawroom_skill_bundle.sh)
- [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/scripts/clawroom_preflight.py](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/scripts/clawroom_preflight.py)
- [/Users/supergeorge/Desktop/project/agent-chat/docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md](/Users/supergeorge/Desktop/project/agent-chat/docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md)

## 4. Current Product/Runtime Model

Important distinction:

- `OpenClaw` is the persistent agent platform / daemon / multi-surface gateway.
- `runnerd` is our own managed room supervisor.
- `openclaw_bridge` is our adapter between ClawRoom room protocol and OpenClaw runtime participation.

Current intended release-grade path:

- OpenClaw as gateway/runtime
- `runnerd` supervises the room worker
- `openclaw_bridge` participates in the room

Important conclusion:

- OpenClaw itself is persistent
- but persistence of the OpenClaw daemon does **not automatically mean** the room is being persistently polled/responded to
- durable room execution still depends on the managed path actually being attached

## 5. Fresh OpenClaw Test That Just Happened

Latest fresh-session test summary:

- a fresh OpenClaw installed `clawroom` successfully via `npx skills add heyzgj/clawroom`
- user asked it to create a room to sync owner information with another OpenClaw
- host behavior and guest behavior were both still flawed

Critical example room:

- `room_d9cebc4772e2`

Observed problems from that run:

### Host problems

1. At first it incorrectly asked for the **other OpenClaw's invite link**, even though the user explicitly asked it to create a room as host.
2. It said preflight was okay only via **shell fallback**, not a managed release-grade path.
3. After room creation it did not continue autonomously.
4. After guest joined, host did not naturally keep polling and replying.
5. User had to manually send "check一下" style nudges to get additional behavior.

### Guest problems

1. Guest joined from the invite successfully.
2. Guest did not proactively continue collaboration.
3. Guest did not keep reacting to room events by itself.
4. User again had to manually prompt it to check.
5. Guest ended up reporting the host as offline / waiting instead of naturally sustaining dialogue.

This means the room flow looked like:

- install succeeded
- create succeeded
- join succeeded
- sustained collaboration failed

That is currently the core product gap.

## 6. Root Causes Identified So Far

### Root cause A: the skill scope is too wide

Current skill mixes too many jobs:

- create
- join
- watch
- contacts
- whitelist
- connect
- managed fallback rules
- compatibility paths
- general multi-runtime guidance

This causes model confusion about which phase it is in.

Consequence:

- host sometimes behaves like guest
- create path sometimes asks for invite links
- language flips between human-facing and protocol-facing

### Root cause B: OpenClaw-only golden path is not yet enforced hard enough

For the installable OpenClaw product path, the model should probably be forced into:

- OpenClaw-only
- managed-only
- no shell-fallback continuation for release-grade create/join

But right now the skill still allows:

- `ready_candidate`
- shell fallback
- compatibility-style continuation

Consequence:

- the room starts in a degraded mode
- the user sees something that "works"
- but it is not actually a durable conversation loop

### Root cause C: "daemon exists" is being conflated with "managed room loop exists"

OpenClaw daemon is persistent, yes.  
But the actual room still needs:

- a durable poll/reply worker
- or a correctly attached managed bridge path

Without that:

- create can succeed
- join can succeed
- but the conversation won't naturally keep going

### Root cause D: skill wording is too long and too technical

Even after multiple rounds of cleanup, the current skill still contains too much:

- technical branching
- internal protocol language
- fallback logic
- old compatibility assumptions

Consequence:

- invite blocks become too long
- host asks the wrong question first
- output sometimes leaks implementation details

## 7. Minimum Requirements vs Recommended Setup

### Minimum requirements to run ClawRoom at all

At minimum, the runtime needs:

- OpenClaw runtime
- script execution
- writable persistent storage
- daemon/background-safe process model
- ClawRoom API reachability

### Recommended release-grade configuration

This is the currently recommended configuration:

- OpenClaw runtime
- `runnerd`
- `openclaw_bridge`
- ClawRoom installable skill
- preflight result: `ready_managed`

### Preflight semantics

Preflight script:

- [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/scripts/clawroom_preflight.py](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/scripts/clawroom_preflight.py)

Statuses:

- `ready_managed`
  - intended release-grade path
- `ready_candidate`
  - compatibility/debug only
- `blocked`
  - should not create/join

Important current insight:

- for the OpenClaw main product path, allowing `ready_candidate` to continue is probably a mistake

## 8. What Is Already Good Enough vs Not Good Enough

### Good enough

- managed room architecture
- runnerd supervision model
- bridge-based managed execution
- artifact evaluation logic
- installable repo export/publish path

### Not good enough

- fresh OpenClaw host flow
- fresh OpenClaw guest sustained collaboration
- invite brevity and language fit
- "create -> join -> ongoing polling -> close" on a real fresh OpenClaw runtime

## 9. What Another Agent Should Look At First

If another agent is analyzing the problem, the best starting points are:

1. the main skill
   - [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/SKILL.md)

2. managed path guidance
   - [/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/references/managed-gateway.md](/Users/supergeorge/Desktop/project/agent-chat/skills/clawroom/references/managed-gateway.md)

3. OpenClaw bridge loop
   - [/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge/src/openclaw_bridge/cli.py](/Users/supergeorge/Desktop/project/agent-chat/apps/openclaw-bridge/src/openclaw_bridge/cli.py)

4. runnerd supervision
   - [/Users/supergeorge/Desktop/project/agent-chat/apps/runnerd/src/runnerd/service.py](/Users/supergeorge/Desktop/project/agent-chat/apps/runnerd/src/runnerd/service.py)

5. prior hard-earned lessons
   - [/Users/supergeorge/Desktop/project/agent-chat/docs/progress/DEBUG_LESSONS.md](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/DEBUG_LESSONS.md)
   - [/Users/supergeorge/Desktop/project/agent-chat/docs/progress/RUNNERD_E2E_LOG.md](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/RUNNERD_E2E_LOG.md)

## 10. Strong Current Hypothesis

The fastest path to a real product fix is probably:

1. narrow the installable skill to **OpenClaw-only**
2. narrow it further to **managed-only**
3. do **not** let OpenClaw continue create/join on `ready_candidate`
4. split out contacts/whitelist/watch into separate skill(s) or a secondary skill later
5. make create/join/watch the only mainline behavior in the primary skill

In other words:

- fewer branches
- fewer runtime modes
- fewer roles
- one durable OpenClaw path

## 11. Recommended Immediate Questions for the Next Agent

The next agent should answer these first:

1. Should the main installable skill be split into:
   - `clawroom-core`
   - `clawroom-contacts`
   - `clawroom-watch`
   or at least logically reduced to just create/join/watch?

2. Should `ready_candidate` be a hard stop for OpenClaw create/join in the main skill?

3. In the fresh OpenClaw test, why did create and join work but durable polling/reply did not continue?
   - Is the skill not invoking managed path?
   - Is bridge attachment not actually happening?
   - Is the room being created from the chat surface directly instead of via managed worker?

4. Should host and guest both be forbidden from continuing unless the room is verified as managed-attached?

## 12. Bottom Line

This project is **not stuck at zero**.

It already has:

- a real working managed architecture
- real local managed E2E success
- installable skill publishing
- improved artifact quality

What is still missing is not the backend idea.  
What is missing is a **tight, product-grade OpenClaw skill surface** that forces the successful path instead of allowing too many fallback behaviors.
