# Dogfood Report #001: Lead Agent → 5 Parallel Codex Workers

**Date**: 2026-03-13
**Lead Agent**: Claude Code (Opus 4.6) — main conversation
**Workers**: 5 Claude Code agents dispatched via `Agent` tool with `isolation: "worktree"`
**Outcome**: All 5 workers FAILED — 0 code written

## What Happened

1. Lead agent (me) decomposed the sprint into 5 independent tasks
2. Dispatched 5 parallel agents with `run_in_background: true` and `isolation: "worktree"`
3. Each agent was given a detailed task prompt with specific files to create/modify
4. All 5 agents ran for 1-3 minutes, read the codebase successfully, designed their implementation
5. All 5 agents were **denied Write, Edit, and Bash permissions** in their worktrees
6. Each agent returned a detailed spec of what it *would have* built, but zero files changed

## Root Cause

The `isolation: "worktree"` mode creates a git worktree for the agent, but the agent still inherits the parent session's **permission model**. The user had not pre-approved write operations for subagents, and subagents cannot prompt the user for approval (they run in background).

## Learnings

### L1: Permissions are the #1 blocker for autonomous agent swarms
Worker agents that can't write files are useless. The permission grant must happen **before** dispatch, not at execution time. This is the single biggest friction point in the lead→worker delegation pattern.

**Product implication**: ClawRoom's mission rooms need to carry a **capability/permission manifest** so the lead can declare what the worker is authorized to do upfront.

### L2: Worktree isolation works for reads, fails for writes
The worktree mechanism correctly isolated each worker's file reads — they could all explore the codebase independently. But the value of isolation is zero if the agent can't modify anything.

**Product implication**: The room creation payload should include an `authorized_actions` field that the runtime enforces.

### L3: Background agents can't negotiate permissions
When an agent hits a permission wall, it can't escalate to the user — it just fails silently and reports what it wanted to do. This is a fundamental limitation of fire-and-forget dispatch.

**Product implication**: The lead agent needs a way to pre-authorize bounded actions for workers, or workers need a structured escalation channel (which is exactly what ASK_OWNER in ClawRoom provides).

### L4: "Spec-only" agents are surprisingly useful
Even though no code was written, each agent produced a detailed implementation spec: exact file paths, line-level changes, SQL schemas, API contracts. The lead agent (me) then executed all 5 specs serially in ~15 minutes. This is actually a valid workflow: **agents as architects, lead as executor**.

**Product implication**: "Planning rooms" (read-only task rooms where the worker produces a spec, not code) could be a first-class concept.

### L5: The lead agent can recover from total worker failure
Despite all 5 workers failing, the lead agent completed all the work by pivoting to serial execution. The mission wasn't blocked — just slower. This validates the lead agent pattern: it's resilient.

### L6: Serial execution by one agent was ~3x faster than parallel workers who all failed
5 parallel workers: ~3 min each × 5 = 15 min wall time, 0 output
1 lead agent serial: ~15 min total, 100% output
Net: parallel dispatch had negative ROI this round due to permission overhead.

## What to Change

1. **Pre-grant write permissions**: Before dispatching worker agents, ensure the permission model allows file creation/editing in worktrees
2. **Implement capability manifest in room creation**: `authorized_actions: ["read", "write", "execute"]`
3. **Add structured escalation**: When a worker is blocked, it should write a structured escalation message to the room, not just return a text blob
4. **Consider "spec rooms"**: Rooms where the expected outcome is a spec document, not code execution
5. **Test with `allowedPrompts` in Agent tool**: The Agent tool supports `allowedPrompts` for pre-granting permissions — use this next time

## Metrics

| Metric | Value |
|--------|-------|
| Workers dispatched | 5 |
| Workers that produced code | 0 |
| Total agent compute time | ~8 min |
| Lead agent recovery time | ~15 min |
| Files created by lead | 4 new + 4 modified |
| Lines of code written | ~650 |
| Compile errors | 0 |
