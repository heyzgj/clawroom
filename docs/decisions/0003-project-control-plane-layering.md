# ADR 0003: Project Control Plane Layering

## Status
Accepted (directional; implementation in progress)

## Context

ClawRoom began as a bounded meeting room for two agents. Real usage, repeated Telegram/OpenClaw end-to-end tests, and runner-plane debugging exposed a larger product truth:

- owners want more than a single room
- owners want many local and cloud agents to collaborate across one project
- bounded rooms remain valuable, but they are not the whole owner experience
- execution continuity, recovery, and ops visibility cannot stay buried in skills or ad-hoc bridge logic

At the same time, external protocols and adjacent systems clarify the layer boundaries:

- A2A is strong for interop, task semantics, and capability negotiation
- Relay is strong for brokered runner lifecycle and message transport
- neither one replaces ClawRoom's room truth, owner control, or structured outcome model

## Decision

ClawRoom adopts a four-layer product architecture:

1. **Room Core**
   - bounded collaboration primitive
   - lifecycle, transcript, stop rules, structured outcomes, owner escalation

2. **Runner Plane**
   - participant execution attachment
   - attempts, leases, renew/release, replacement, certification, health, logs

3. **Project Control Plane**
   - owner-facing top layer
   - project roster, room graph, decisions, follow-ups, budget posture, intervention surface

4. **Interop Plane**
   - outer compatibility layer
   - A2A-style discovery, capability negotiation, and task/context exchange

The room remains the atomic collaboration unit.
The project becomes the primary owner-facing orchestration surface.

## Why This Is Better

1. **Matches real owner needs**
   - owners think in projects, not isolated transcripts

2. **Preserves current strengths**
   - room truth, owner visibility, and structured outcomes remain first-class

3. **Separates concerns cleanly**
   - room truth is not overloaded with runner survivability
   - interop does not take over execution control

4. **Supports hybrid agent fleets**
   - local Codex / Claude Code / OpenClaw
   - cloud OpenClaw in Telegram / Discord / Feishu
   - future hosted runtimes

5. **Creates a credible path to zero silent failure**
   - reliability belongs in the runner plane
   - owner trust belongs in the project control plane

## Consequences

1. The product should no longer be described only as "a room product".
2. Current roadmap items around room truth and runner truth remain valid, but are now stepping stones toward the project control plane.
3. Compatibility paths stay supported, but only certified runtimes count as product-owned reliable paths.
4. A2A remains a future outer adapter, not a core rewrite target.

## Follow-ups

1. Add a formal `Phase 6: Project Control Plane` to the roadmap.
2. Keep room truth and runner truth as prerequisites before broadening interop.
3. Design project-level entities:
   - project
   - roster
   - room graph
   - decision log
   - follow-up queue
4. Promote project-level ops:
   - active rooms
   - blocked rooms
   - takeover rooms
   - recovery backlog
   - cost posture

## Evidence

1. Internal proposal:
   - `docs/proposals/project_control_plane_thesis.md`
2. Current architecture:
   - `docs/context/ARCHITECTURE.md`
3. Current roadmap:
   - `docs/progress/ROADMAP.md`
4. Real Telegram/OpenClaw lessons:
   - `docs/progress/DEBUG_LESSONS.md`
   - `docs/progress/TELEGRAM_E2E_LOG.md`
