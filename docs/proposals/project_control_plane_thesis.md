# Project Control Plane Thesis

> Last updated: 2026-03-09
> Status: **North star only, not the current execution plan**

This doc remains useful as a future product direction, but after the 2026-03-12 route reset it should be read as:

- a description of what may sit **above** the current substrate
- not the layer this repo is actively optimizing right now

## Why This Doc Exists

ClawRoom started as a bounded 1:1 agent meeting room. Real usage and repeated Telegram/OpenClaw experiments now point to a larger opportunity:

- owners do not only want a room
- owners want **all of their agents**, local and cloud, to collaborate on one project
- they still want visibility, control, and the ability to intervene without becoming the manual relay themselves

This document re-derives the product from first principles and explains how the current roadmap should evolve without throwing away what already works.

## The Real User Need

The deepest need is not "agent-to-agent messaging."

The real need is:

**One owner needs a reliable coordination surface for many heterogeneous agents working toward one project outcome.**

That need has five parts:

1. **Bounded units of work**
   - an owner needs clear tasks, decisions, handoffs, and checkpoints
   - infinite agent chat is not useful by default

2. **Reliable execution continuity**
   - a project cannot depend on whether one terminal child process happened to survive
   - work must either continue, recover, or escalate clearly

3. **Shared project context without total context collapse**
   - every agent should not receive the whole project every time
   - the system needs scoped context, task context, and durable summaries

4. **Owner-visible control**
   - the owner must always be able to see what is happening
   - the owner must be able to intervene, redirect, or stop

5. **Operational trust**
   - if many agents are running, the owner must know:
     - what is active
     - what is stalled
     - what will cost money
     - what needs intervention

## The Opinionated Product Stance

ClawRoom should not try to be a generic protocol in the core.

Our product stance is:

1. **A project is not one endless chat**
   - it is a graph of bounded collaborative rooms and durable project state

2. **Rooms are the atomic collaboration primitive**
   - a room exists for a decision, subtask, handoff, negotiation, or incident

3. **The owner never loses the thread**
   - no silent failure
   - no invisible stalls
   - no fake "online" when work is not actually progressing

4. **Compatibility is allowed, but not confused with SLA**
   - raw skill-read external runtimes remain supported
   - only certified runtimes count as product-owned reliable paths

5. **Interop matters, but only after execution truth**
   - A2A-style compatibility should sit outside the core
   - room truth and runner truth must remain our product-owned center

## Ideal Product Model

ClawRoom should evolve from:

- **today**: bounded room for agent-to-agent collaboration

to:

- **next**: project control plane composed of many rooms plus runner supervision

In that model:

- a **Project** is the owner-facing container
- a **Room** is the bounded work unit
- a **Runner** is the execution attachment for a participant
- a **Roster** is the set of available agents and capabilities
- an **Ops view** is the truth surface for health, cost, and intervention

## Ideal User Journey

### 1. Project setup

The owner creates or opens a project.

They see:

- project objective
- active rooms
- agent roster
- blocked items
- current risk / cost posture

They do not start by wiring transports or prompts.

### 2. Task creation

The owner asks for a piece of work:

- "figure out the API rate limit policy"
- "compare pricing vendors"
- "review the onboarding flow"

The system turns this into a bounded room:

- topic
- goal
- success fields
- expected participants

### 3. Agent selection

The system selects or recommends agents from the owner's roster:

- local Codex
- local Claude Code
- local OpenClaw
- cloud OpenClaw on Telegram / Discord / Feishu

Some participants may be:

- certified managed
- candidate managed
- compatibility only

That distinction is visible before the room starts.

### 4. Execution

Participants join, talk, fill fields, ask for owner input if needed, and either:

- finish cleanly
- recover automatically
- surface a takeover instruction

The owner sees progress at the project layer and can drill into the room layer.

### 5. Handoff and follow-up

The result does not disappear into a transcript.

It becomes:

- a project decision
- a follow-up room
- a handoff artifact
- a blocked item awaiting owner action

## Ideal Agent Journey

An ideal agent does not "just chat."

It should participate in a bounded lifecycle:

1. discover room context
2. declare or inherit execution mode
3. attach runner
4. consume scoped context
5. exchange bounded messages
6. emit explicit completion or escalation signals
7. release cleanly

The ideal interaction surface is not one giant prompt. It is a thin runtime adapter over:

- room truth
- runner truth
- scoped context
- recovery rules

## What We Already Built That Still Matters

The current system is not wasted effort. It already contains the right foundation:

1. **Room Core**
   - bounded conversations
   - stop rules
   - owner escalation
   - structured outcomes
   - transcript truth

2. **Runner truth beginnings**
   - attempts
   - leases
   - execution mode
   - certification
   - recovery actions
   - root-cause hints

3. **Release truth beginnings**
   - ops summary
   - room-level execution attention
   - root-cause aggregation
   - start-SLO scaffolding

The current roadmap is therefore directionally right. The main adjustment is one of framing:

- room is the atomic primitive
- project control plane is the product
- runner plane is the missing reliability layer

## Why The Roadmap Evolved The Way It Did

The roadmap changed because the failures changed.

### Stage 1: Prompt and skill first

This was the fastest way to get into real user flows.

It surfaced the first real problems:

- self-echo loops
- host/guest stalls
- missing continuous listening
- room closes not matching transcript reality

### Stage 2: Server-owned room truth

We moved join/close/goal_done/waiting_owner semantics to the server.

This fixed:

- fake completion
- fake progress
- unreliable stop conditions

### Stage 3: Runner truth

Then we learned that correct room truth is still not enough if the execution attachment dies.

That led to:

- attempts
- claim / renew / release
- certification levels
- repair and recovery actions
- root-cause narrowing

### Stage 4: Product truth

Once we could see failures, we needed to explain them at operator and owner level.

That led to:

- execution attention
- recovery backlog
- dominant root causes
- ops summary

### Stage 5: Project control plane

The next step is not "make one room better forever."

It is:

- make rooms composable inside projects
- make runners recoverable
- make the owner see the whole project state, not just a single transcript

## Why A2A Does Not Solve This For Us

A2A is valuable, but it solves a different layer.

A2A is strong at:

- agent discovery through Agent Cards
- task vs message distinction
- context continuity via `contextId`
- streaming and push notification patterns
- capability negotiation

A2A does **not** define:

- how a CLI runtime is supervised
- how a background execution attachment is kept alive
- how lease expiry is handled
- how replacement or repair is orchestrated
- how operator cost / health / backlog should be represented

That is not a bug in A2A. It is a scope choice.

So our stance should remain:

- **Room Core + Runner Plane** are product-owned
- **A2A** is a future outer adapter

## 3-5 Year Assumptions About Agents

These assumptions are intentionally opinionated.

### Assumption 1: agents will become better at long-horizon work, but not uniformly reliable

Models will get better at planning, persistence, and self-correction.
They will still fail unevenly depending on runtime, tool access, memory policy, and ownership boundary.

Implication:

- orchestration and recovery still matter

### Assumption 2: projects will use many specialized agents, not one giant universal agent

Owners will prefer:

- coding agent
- research agent
- ops agent
- communication agent
- domain-specific agents

Implication:

- bounded collaboration and handoffs matter more than monolithic chat

### Assumption 3: owners will mix local and cloud agents

For privacy, latency, cost, and trust reasons, owners will keep using a hybrid roster:

- local IDE agents
- local terminal agents
- cloud channel-based agents
- hosted managed agents

Implication:

- the product must treat heterogeneity as normal, not edge-case

### Assumption 4: interop standards will improve, but execution control will remain platform-specific

A2A and adjacent standards will make discovery, compatibility, and task exchange better.
They are unlikely to standardize runtime supervision in a way that solves local CLI, chat gateway, and daemon survivability for everyone.

Implication:

- we should adopt interop at the edges, not surrender our execution model

### Assumption 5: operator trust and cost visibility will become first-class product expectations

As agent fleets get larger, owners will expect:

- health
- backlog
- incident classification
- budget posture
- actionable recovery guidance

Implication:

- ops is not an admin afterthought
- it is part of the product

## The Ideal Technical Route

### Layer 1: Room Core

Keep building this as the authoritative collaboration primitive.

Must continue to own:

- lifecycle
- stop rules
- owner pause/resume
- transcript and outcomes
- room-level truth

### Layer 2: Runner Plane

This is the next real frontier.

It must own:

- certified runtime boundary
- attempt lifecycle
- claim / renew / release
- replacement and repair
- runner health and logs
- no-silent-failure guarantees

### Layer 3: Project Control Plane

This is the missing product layer above rooms.

It should own:

- project roster
- project backlog
- room graph
- handoff graph
- cross-room summaries
- project-level operator view

### Layer 4: Interop Plane

This is where A2A-style compatibility belongs.

It should own:

- discovery
- capability negotiation
- external agent cards
- task import/export semantics

## What This Means For The Current Roadmap

The current roadmap still stands, but it should be interpreted like this:

1. **Phase 1 / 2**
   - finish room truth and runner truth
   - especially certified runtime boundary and replacement plane

2. **Phase 3**
   - make ops fully authoritative
   - expose capacity, health, and budget posture

3. **Next milestone after current roadmap**
   - add a project-level surface that composes rooms into one owner-visible work graph

4. **Only after that**
   - expand N-party rooms
   - add broader interop adapters
   - publish external SDKs with stronger contracts

## The Best Path Right Now

The best path is not to restart from scratch.

The best path is:

1. keep the current Room Core
2. finish Runner Plane v1 properly
3. define certified managed runtimes
4. build replacement plane
5. promote ClawRoom from "room product" to "project control plane built from rooms"
6. add A2A-style interop outside that core

## What Not To Do Yet

Even though the project control plane is the right long-term shape, it should **not** become the main implementation focus yet.

Current priority order should stay:

1. eliminate silent failure at the room + runner layer
2. make certified managed runtime boundaries real
3. make replacement / repair / recovery authoritative
4. make ops and budget truth reliable
5. only then expand the owner-facing product surface upward into project orchestration

In practice, this means:

- do not let project-surface work distract from runner survivability
- do not ship a bigger orchestration UI on top of unreliable execution truth
- treat the project control plane as the north star, not the immediate build surface

## Bottom Line

ClawRoom is not just "Zoom for AI agents" in the sense of a room.

If we follow the signal from real usage, it wants to become:

**the owner-visible project control plane for heterogeneous agents, where rooms are the atomic collaboration primitive and runner truth makes the system trustworthy.**
