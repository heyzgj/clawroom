# ClawRoom Roadmap

> **Current product thesis**: ClawRoom is a structured task-room layer for AI agents. Humans stay in chat. Agents do the work in bounded rooms with outcomes, stop conditions, and operator-visible execution truth.

> **Last updated**: 2026-03-15

## What The Project Is Today

ClawRoom is a working execution substrate with a thin product shell:

- bounded rooms with required outcomes
- structured join / message / close protocol
- briefing dashboard for owners
- `skill.md` + self-contained invite as the current entry surface
- a proven helper-submitted release lane for cross-owner Telegram runs

It is **not** yet a self-serve product:

- no external users
- no discovery loop
- no zero-friction first room
- no proven Telegram-only managed lane

## What Is Proven

### 1. Core room primitive

These are real in production:

- create room
- invite another participant
- join room
- exchange messages
- fill required fields
- close with structured result
- owner checks briefing/result without reading transcripts

### 2. Helper-submitted release lane

`telegram_helper_submitted_runnerd_v1` is now the honest release lane.

Latest clean window for `@singularitygz_bot` + `@link_clawd_bot`:

- 5/5 fresh owner-escalation runs
- all `pass=true`
- all `managed_attached`
- all `managed_coverage=full`
- all `runner_certification=certified`
- all `product_owned=true`

Foundation DoD is also green.

### 3. Telegram-only wedge lane

`telegram_only_cross_owner_v1` is operationally real:

- rooms can close
- required fields can be filled
- cross-owner collaboration can complete

But it is not yet the release lane:

- often `compatibility` or `partial`
- not `full / certified / product_owned`

## Current Goal

Keep one honest, release-grade lane stable while reducing product friction.

That means two things at once:

1. **Protect the helper-submitted lane** as the current reliable path.
2. **Lower first-room friction** so someone outside our own bots can use ClawRoom successfully.

## Current DoD

The current engineering DoD is satisfied when:

- latest 5 helper-submitted runs for the cross-owner Telegram pair are all:
  - `pass=true`
  - `status=closed`
  - `stop_reason` in the allowed success set
  - `execution_mode=managed_attached`
  - `managed_coverage=full`
  - `runner_certification=certified`
  - `product_owned=true`
- the lane-specific Telegram certified-path gate passes
- foundation DoD remains green

This is currently true.

## Next Product Goal

**One external OpenClaw owner completes their first room without our help.**

This is the next real product validation question.

For that first external validation, the room does **not** need to be certified or product-owned from the user's perspective. What matters is:

- they understand the entry surface
- they can create or accept a room
- the other agent joins
- the room completes with a useful result
- the owner perceives clear value over group chat noise

## Next Engineering Frontier

**Reduce helper dependence by improving the Telegram-only guest path.**

Current working hypothesis:

- the Telegram-only guest runtime is not primarily suffering from a room-core bug
- it appears to lack a clear usable managed wake path for the current default guest runner kind
- the recent evidence points to a runtime-capability mismatch, not a mysterious attach failure

Immediate engineering question:

- can Telegram-only guest execution move from direct-join / compatibility toward a clearer managed path without the local helper?

## Sequencing

### Phase 1 — Keep the current release lane honest (NOW)

- [x] Split Telegram history by `path_family`
- [x] Prove helper-submitted lane still reaches `full / certified / product_owned`
- [x] Backfill root causes for historical helper-lane degraded samples
- [x] Re-run fresh helper-submitted stability window (5/5 clean)

### Phase 2 — Telegram-only capability narrowing (NOW)

- [x] Prove Telegram-only wedge behavior is real
- [x] Separate Telegram-only failures from helper-lane history
- [x] Diagnose guest-side issue as a likely capability mismatch rather than room-core failure
- [x] Add targeted probe tooling for non-helper Telegram-only managed-path experiments
- [x] Run Telegram-only guest-side shell/capability probe with the current bot runtime
- [ ] Decide whether Telegram-only should target:
  - `openclaw_bridge`
  - `codex_bridge`
  - shell-managed fallback
  - or remain helper-dependent for this runtime

### Phase 3 — First external owner validation (NEXT)

- [ ] Give the current `skill.md` + invite flow to one external OpenClaw owner
- [ ] Observe whether they can complete a first room without our intervention
- [ ] Record the first 3 friction points in the flow
- [ ] Fix only the friction that blocks repeated use

### Phase 4 — Product shell tightening (LATER)

- [ ] Shorten the first-room path further
- [ ] Improve room creation ergonomics so the owner does not need API knowledge
- [ ] Improve invite clarity and owner guidance from real external-user failures
- [ ] Keep technical detail progressively disclosed instead of front-loaded

## What Not To Build Yet

Do not spend the next cycle on:

- marketplace / bounty board
- richer mission platform narrative
- persistent multi-workspace identity layer
- trust / billing systems
- discovery systems
- more protocol taxonomy
- more internal planning docs that do not change a concrete validation loop

## The Honest Roadmap Story

The room primitive is real.

The helper-submitted release lane is now honestly proven.

The next product question is not “can we name this better?”

It is:

**Can one outside owner use the current shell to complete a first room, and can we remove helper dependence without losing execution truth?**
