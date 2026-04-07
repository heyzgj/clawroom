# Engineering Assessment (2026-03-12)

## Purpose

Capture a high-level engineering assessment of the current ClawRoom codebase after the foundation DoD and Telegram-certified DoD passed, so future roadmap decisions have an explicit baseline.

This document answers three questions:

1. Where are we over-engineered?
2. Where are we under-engineered?
3. Where are we exactly right for the current phase?

It also records how OpenAI Symphony fits into the current architecture discussion.

## Current Positioning

ClawRoom is currently best understood as a **reliable bounded collaboration substrate**:

- Room Core owns bounded work-thread truth.
- Runner Plane owns execution truth and recovery.
- Release Truth owns ops and evaluation truth.

It is **not yet** the full product shell for:

- project control
- artifact graph / adoption graph
- network / marketplace

Those remain later layers.

## Over-Engineered

### 1. Semantic density is reasonable, but code-bearing structure is too monolithic

The biggest over-engineering issue is not the existence of the semantics, but where they live.

- [`worker_room.ts`](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_room.ts) mixes:
  - room lifecycle
  - relay semantics
  - attention/recovery derivation
  - snapshot shaping
  - protocol hard rules
- [`worker_registry.ts`](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_registry.ts) mixes:
  - registry storage
  - derived summary calculation
  - root-cause aggregation
  - ops view shaping

The semantics are mostly right, but the files are already carrying too much at once. That raises the cost of changing any one rule safely.

### 2. Future-layer architecture is more detailed than current product certainty

[`ROADMAP.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/ROADMAP.md), [`ARCHITECTURE.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/context/ARCHITECTURE.md), and [`PRD.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/context/PRD.md) already describe:

- Zoom Foundation
- Interop
- Project Control Plane

That thinking is useful, but it is more detailed than the first wedge has proven. The cost is that discussion can drift into future layers too early, while the current repo is still mostly foundation.

### 3. Compatibility and migration surface still carry maintenance tax

There are still multiple historical / compatibility surfaces:

- duplicated skill/public copies
- legacy naming / API compatibility layers
- multiple evaluation views for different system epochs

These are not wrong, but they add meaningful cognitive load for a small, fast-moving codebase.

### 4. Diagnostics sophistication is slightly ahead of end-user product maturity

The project already has strong:

- E2E logs
- debug lessons
- path-specific evaluators
- root-cause summaries

That is good and worth keeping, but it is a little ahead of the current user-facing product shape. This mismatch is part of why progress can sometimes feel abstract even when the foundation is improving.

## Under-Engineered

### 1. `runnerd` is still too thin relative to the intended replacement plane

[`service.py`](/Users/supergeorge/Desktop/project/agent-chat/apps/runnerd/src/runnerd/service.py) now supports meaningful behavior:

- wake
- owner reply
- cancel
- single auto-restart
- restart exhaustion
- replacement lineage

But it still does not yet provide a full replacement plane:

- no durable queue
- no DLQ
- no multi-attempt orchestration
- no crash-resume replay
- no independent worker scheduler

This is the clearest under-engineered area, and it is also the current strategic bottleneck.

### 2. Registry ingestion and replay are still not fully authoritative

The system is much more observable now, but registry truth is still not fully at the level of a final authoritative control plane. Some failure handling is still best-effort rather than replay-first.

That remains visible in [`KNOWN_ISSUES.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/KNOWN_ISSUES.md).

### 3. Telegram gateway UX is still infra-shaped

The Telegram-first certified path now works, but it still feels like:

- a clean infra flow
- a debuggable operator path

more than:

- a polished end-user product journey

That is acceptable for the foundation phase, but it is still under-engineered from a product experience standpoint.

### 4. The first wedge is less crisp than the engineering foundation

The codebase now knows what its failure model is much better than it knows what its first user-facing pitch is.

That is a sign that the foundation has outpaced wedge definition.

### 5. Capacity and budget are not yet fully first-class architecture constraints

The project has already learned that Cloudflare free-tier assumptions can distort evaluation. Mitigations exist, but the architecture is still moving from:

- "we discovered the limit and patched around it"

to:

- "budget, throughput, and storage/read pressure are first-class design constraints"

## Perfectly Engineered For The Current Phase

### 1. Room truth is exactly where it should be

[`worker_room.ts`](/Users/supergeorge/Desktop/project/agent-chat/apps/edge/src/worker_room.ts) and [`PROTOCOL.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/spec/PROTOCOL.md) now encode the right kind of hard truth:

- join gate
- close idempotency
- strict completion rules
- waiting-owner semantics
- message bounds

This is exactly the kind of logic that should live in the system, not in prompts.

### 2. Path grading is exactly right

The separation between:

- `certified`
- `candidate`
- `compatibility`

is one of the best decisions in the repo. It prevents a very common self-deception:

> if it can run once, maybe it is already safe to promise

That boundary is now encoded in protocol, runtime metadata, and evaluation.

### 3. The test strategy is unusually mature

[`TEST_STRATEGY.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/spec/TEST_STRATEGY.md) is strong because it evaluates seams instead of only components:

- component
- contract
- bridge harness
- survivability
- live E2E

That is exactly the right way to test a system whose real failures happen between layers.

### 4. The project records its own lessons well

The combination of:

- [`DEBUG_LESSONS.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/DEBUG_LESSONS.md)
- [`KNOWN_ISSUES.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/KNOWN_ISSUES.md)
- [`CHANGELOG.md`](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/CHANGELOG.md)

is a real strength. The system is not only evolving; it is preserving the reasons behind the evolution.

## What OpenAI Symphony Changes

Symphony is useful, but it does **not** replace the current foundation work.

Based on the public README ([GitHub README](https://raw.githubusercontent.com/openai/symphony/main/README.md)), Symphony is best understood as an **upper-layer orchestration framework** for autonomous implementation runs:

- routines
- isolated execution runs
- shared memory / handoffs
- project-work orchestration

That is valuable for the layer *above* the current foundation.

### Symphony helps with

- multi-agent work orchestration
- routine / handoff framing
- work-centric coordination instead of chat-centric coordination
- an upper-layer mental model closer to "manage work, not agents"

### Symphony does not solve the current hardest problem

Symphony does not directly solve the part of the system that is currently most important for ClawRoom:

- cross-owner wake paths
- runtime certification
- runner supervision across heterogeneous runtimes
- repair / replacement semantics
- no-silent-failure ops guarantees

So for ClawRoom, Symphony should be treated as:

- a useful reference for the future orchestration layer
- not a substitute for the current Runner Plane and Release Truth work

## Practical Conclusion

For the current phase, the right priorities are still:

1. make `runnerd` and the replacement plane more real
2. keep certified vs candidate boundaries hard
3. continue raising ops truth toward authoritative status

The current codebase is **not** over-engineered in spirit. It is over-engineered mostly in:

- file-level monolith size
- future-layer narrative density

It is under-engineered exactly where it matters most next:

- durable runner orchestration
- replacement/recovery execution
- authoritative operator truth

And it is exactly right in the places that foundation systems most often get wrong:

- room truth
- path grading
- test philosophy
