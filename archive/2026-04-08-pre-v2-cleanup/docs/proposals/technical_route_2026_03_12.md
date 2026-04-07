# Technical Route Reset (2026-03-12)

## Summary

After comparing the current ClawRoom codebase and roadmap with public 2026 references:

- [OpenAI Symphony](https://raw.githubusercontent.com/openai/symphony/main/README.md)
- [Agent Relay README](https://raw.githubusercontent.com/AgentWorkforce/relay/main/README.md)
- [Agent Relay Architecture](https://raw.githubusercontent.com/AgentWorkforce/relay/main/ARCHITECTURE.md)
- [A2A README](https://raw.githubusercontent.com/google/A2A/main/README.md)
- [Karpathy agenthub](https://github.com/karpathy/agenthub)
- [Paperclip](https://paperclip.ing/)

the strict route is now:

**ClawRoom should first become a cross-runtime, cross-owner collaboration substrate for AI agents.**

More concretely, the current repo should own the lead/gateway-supervised worker-execution layer beneath any future control plane or artifact hub.

It should **not** currently try to be:

- the full owner control plane
- the artifact hub
- the open network / marketplace

Those are later layers.

## What We Learned From External Systems

### Symphony

Symphony is most useful as a signal that the upper layer should be:

- work-centric
- run-centric
- proof-centric

not chat-centric.

It is strong at:

- routines
- isolated runs
- work orchestration
- proof of work

It does **not** solve our current hardest problem:

- cross-owner wake paths
- runtime certification
- replacement / recovery
- no-silent-failure ops

### Agent Relay

Relay is the strongest reference for the **runner/broker layer**:

- spawn
- release
- status
- ready / idle / exited
- PTY wrapping

It confirms that runner lifecycle is a first-class layer, not a prompt trick.

It does **not** give us our current product truth:

- bounded work contract
- owner escalation semantics
- room result truth
- certified vs candidate grading

### A2A

A2A is the right outer-layer reference for:

- agent discovery
- capability negotiation
- protocol-level async/streaming

It is not the current foundation target because it does not solve execution continuity or recovery.

### agenthub

agenthub is the cleanest signal that future large-scale research / artifact networks should be:

- artifact-first
- DAG-first
- note/review/adoption centric

It is a better mental model for a future hub than a room-first product.

### Paperclip

Paperclip validates that an owner-facing control plane is real demand.

But it is a higher layer:

- projects
- org/goals
- budgets
- orchestration UI

ClawRoom should not currently collapse into that layer.

## The Chosen Layering

### Current repo owns

1. **Room Core**
   - bounded work-thread truth
   - completion / waiting-owner / result semantics

2. **Runner Plane**
   - wake
   - attach
   - heartbeat
   - replacement / repair
   - certified vs candidate grading

3. **Release Truth**
   - ops
   - evaluation
   - capacity / risk posture

### Explicitly deferred

1. **Owner control plane**
   - global inbox
   - project dashboard
   - roster UI

2. **Artifact hub**
   - branch DAG
   - adoption graph
   - proof graph

3. **Open network**
   - discovery
   - reputation
   - payment
   - marketplace

## The Updated Product Definition

ClawRoom is currently:

**a reliable async bounded work-thread substrate that lets a lead/gateway wake, supervise, and recover worker agents across different runtimes and owners.**

This means:

- Telegram/Slack/OpenClaw are primarily gateways
- external managed runners are the real execution workers
- rooms are bounded work threads, not the final product shell

## Current Primary Goal

The immediate goal is no longer "make agent chat work."

The immediate goal is:

**make one lead/gateway reliably assign, supervise, recover, and close bounded work executed by worker agents.**

## Near-Term Milestones

### M1. Foundation truth stays green

- keep certified managed path passing
- keep Telegram-certified path passing
- prevent silent failure regressions

### M2. Replacement plane becomes real

- move from single restart toward full:
  - pending
  - issued
  - claimed
  - resolved

### M3. Lead/worker contract gets sharper

- worker wake package
- owner escalation routing
- result/proof packaging
- clear gateway vs runner responsibilities

## What This Route Rejects

For now, we explicitly reject:

1. treating shell-managed Telegram execution as the main release-grade path
2. expanding into project / marketplace / artifact hub before replacement plane is solid
3. using transcript-first language as the primary product framing

## Practical Consequence

The current roadmap should be read as:

- **now**: foundation for supervised worker execution
- **later**: work/run-centric orchestration layer
- **much later**: artifact graph and open network

This keeps the project aligned with the hardest proven problem first and prevents the repo from pretending it is already the full product.
