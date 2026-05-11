# ADR 0001 — Direct Mode Replaces Bridge-as-LLM-Runner

- **Status**: Accepted
- **Date**: 2026-05-11
- **Supersedes**: v3 bridge-as-LLM-runner architecture in
  `skill/scripts/bridge.mjs`
- **Author**: Claude Code (host) + Codex (guest, evaluator)
- **Origin rooms**: `t_83e68365-8ff` (architectural decision),
  `t_0593a0a6-a3d` (full lesson cross-check), `t_bf866856-df0`
  (reflection sync). Transcripts in `docs/progress/v4_t_*.redacted.json`.

## Context

`bridge.mjs` (2507 lines, ~1500 of regex violation guards) is
structurally an embedded LLM runner. It registers a separate OpenClaw
subagent (`clawroom-relay`), depends on model config, a WS gateway, and
a detached process lifecycle. `skill/SKILL.md` says "this owner's
agent" — but the agent that actually speaks in the room is a
*different* model. The fix rate on the regex layer is high: every new
scenario (buyer-as-seller, paid kickoff, date confirmation, print
scope, required interaction, ...) produces a new violation function.

This contradicts the product thesis recorded in MEMORY: **"ClawRoom
plugs into existing agents as a skill; do not rebuild as a standalone
harness agent."**

The first dogfood room (`t_c2d57d3d-e12`) made the contradiction
operational: host bridge failed at startup with
`OpenClaw → clawroom-relay → MiniMax-M2.7 rejected by token plan`.
Architectural failure, not configuration: bridge requires a separate
LLM the maintainer must keep alive.

## Problem

The two failure modes of `bridge.mjs` are now structural:

1. **Live regex wall on agent output**. Every new edge case the model
   produces needs a new `*Violation` function in bridge. Cost grows
   superlinearly with scenarios; the regex layer is brittle to LLM
   regression and to prompt drift across model versions.
2. **Embedded second LLM**. Bridge registers and runs a different
   agent than the one the owner is talking to. This violates the
   product thesis and creates a hidden surface (model config, gateway,
   process lifecycle, runtime-asset hashes) that the owner's primary
   agent has no reason to know about.

## Why `bridge.mjs` Was Reasonable At First

When v3 began, the relay HTTP shape was still moving and we needed to
iterate on agent behavior without pinning the relay surface. Bridge
let us:

- centralize conversation logic while the relay schema stabilized,
- test cross-runtime (local macOS × Railway Linux) by spawning a
  bridge per host,
- experiment with model providers via OpenClaw subagent config.

All three are resolved now. Relay HTTP is stable (T1–T5 passed
2026-04-15). Cross-runtime works. Primary-agent runtimes (Claude Code,
Codex, Cursor, Hermes, OpenClaw used directly) are independently
strong enough to drive a room conversation. `bridge.mjs` keeps
imposing a cost without paying it back.

## Decision

**v4 ClawRoom = Direct Mode.** Three layers, not four:

```text
primary agent      → reasoning, decision, owner approval, final close
sdk / cli          → deterministic transport primitives
watch helper       → persistent listener / wakeup (metadata-only)
relay              → persistent room state + /messages + /events
```

ClawRoom provides layers 2–4. Layer 1 (intelligence) lives in the
owner's primary agent. Always.

Removed from the product path:

- `skill/scripts/bridge.mjs`
- `skill/scripts/launcher.mjs`
- `skill/scripts/clawroomctl.mjs` (bridge-launch logic)
- OpenClaw `clawroom-relay` subagent registration
- Model config dependency
- WS gateway dependency
- `--require-features owner-reply-url` requirement

These move to `legacy/v3-bridge/` after Phase 5 E2E passes. Until then
they stay in place but are not the default. Day-one physical delete is
rejected — see Phase 3 staging.

Added to the product path:

- `relay/worker.ts` — new `GET /threads/:id/events?after=N&wait=20`
  endpoint returning only `{id, from, kind, ts}` (no text, no
  metadata). Invariant 9 enforcement by relay shape.
- `skill/lib/` — thin JS library wrapping relay HTTP, plus typed
  `WatchEvent`, `CloseDraft` schema, deterministic close validator,
  optional pre-send/pre-close lint.
- `skill/cli/clawroom` — bash entrypoint over the lib.
- `evals/invariant9.test.mjs` — release gate: fails if watch ever
  emits / logs / persists message body content.
- `evals/fixtures/*.json` — release gate fixtures converted from
  current bridge violation guards (Phase 4).

## Capability Primitives — 6 for Product-Grade v4

| # | Capability | Notes |
|---|---|---|
| 1 | HTTP transport | `curl` / `fetch` / `urllib` with header + JSON body + 30s+ timeout |
| 2 | Primary-agent reasoning + long context | any reasonable LLM, ≥32k preferred |
| 3 | Durable local state + JSON parse | state file in `~/.clawroom-v4/` |
| 4 | Wait / wakeup | foreground long-poll OR background watcher |
| 5 | Owner-conversation channel | only for dynamic ASK_OWNER; not needed if mandate fully pre-encoded |
| 6 | Structured artifact validation | CloseDraft schema validator + state validator (the close hard wall) |

Minimum runtime to make a simple room = 1–4. Add 5 for dynamic owner
approval. Add 6 for product-grade: without 6, direct mode can mutually
close but cannot prove close quality, so it is not v4 release.

## Boundary Against Regression — Invariants 1–17

Full text in `~/.claude/plans/clawroom-core-rebuild-serene-minsky.md`.
The non-negotiable summary:

1. Owner's primary agent is the agent that represents the owner.
2. ClawRoom does not start a separate LLM.
3. Persistence is for transport / state, never for intelligence.
4. Relay is the source of truth; mechanical only.
5. Owner approval defaults to the owner-agent conversation.
6. Web `/ask-owner` is fallback only.
7. Policy is offline eval + optional deterministic lint. No live regex wall.
8. ClawRoom is runtime-agnostic.
9. **Watch helper is metadata-only.** Enforced by relay `/events` shape
   + typed `WatchEvent` (no text field) + `evals/invariant9.test.mjs`.
10. Fixture eval suite is a release gate.
11. E2E oracle = relay snapshot + local state + runtime-location proof
    + UX artifact (when applicable).
12. Hosted relay admission + quota safety are product requirements.
13. Owner approval is blocking state with provenance, not notification
    copy. Enforced at `clawroom close` hard wall.
14. Bidirectional mandates are first-class for both roles.
15. Watch / wakeup is metadata-only and non-semantic by construction.
16. Direct-mode readiness replaces launcher readiness.
17. **Role custody is non-transferable.** No side may post or close on
    behalf of the peer. Peer-unavailable maps to wait / retry / owner
    clarification / timeout / partial / no-agreement close / new
    invite — never impersonation.

**Future commits that re-introduce any of these patterns must be
rejected in code review:**

- an embedded LLM in the product path,
- a live regex wall on agent output,
- a peer-impersonation shortcut "to complete a stuck room,"
- watch helper that reads message body or composes replies,
- close that posts without CloseDraft validation,
- hosted relay create without admission key.

This ADR is the boundary.

## Consequences

**Positive**:

- Repo simplifies. `bridge.mjs` (2507) + `launcher.mjs` (243) +
  `clawroomctl.mjs` (344) leave the product path.
- ClawRoom becomes runtime-agnostic. Any primary-agent runtime with
  HTTP + reasoning + file r/w + wait can use it.
- Bug surface shifts from regex pile to schema + lint + fixture — all
  deterministic, all testable, all rejectable in review.
- Owner UX matches expectation: the agent the owner is talking to IS
  the agent representing them.

**Negative / risk**:

- Phase 5 E2E surface expands (4 cases including hostile cross-session
  resume) before legacy migration. Until E2E passes, both paths
  coexist.
- `/events` endpoint requires relay deploy + version coordination with
  installed skill clients. Old clients keep using `/messages`.
- Cross-runtime / async owner paths (e.g., Telegram-bot owner) still
  need `owner_url` web fallback. Simplification is for the
  *primary-agent same-session* path, not every path.
- Stronger close validation may surface owner-side mistakes earlier
  (e.g., missing provenance on an agreed term). That's intentional —
  the v3 regex was hiding them.

## Implementation Plan

See `~/.claude/plans/clawroom-core-rebuild-serene-minsky.md` for the
full phase plan. Summary:

- **Phase 0** — this ADR (anchors the decision in repo).
- **Phase 1** — thin lib + CLI + `/events` endpoint + invariant 9 test.
- **Phase 2** — rewrite `SKILL.md` + references for direct mode.
- **Phase 3** — bridge legacy migration (after Phase 5 passes).
- **Phase 4** — fixture eval suite + CloseDraft schema + close
  validator + bidirectional mandate fixtures.
- **Phase 5** — direct E2E (4 cases including hostile cross-session
  resume + role-custody E2E for invariant 17).
- **Phase 6** — public README rewrite + skill manifest cleanup.

## References

- Plan (working): `~/.claude/plans/clawroom-core-rebuild-serene-minsky.md`
- Transcripts:
  - `docs/progress/v4_t_83e68365-8ff.redacted.json`
  - `docs/progress/v4_t_0593a0a6-a3d.redacted.json`
  - `docs/progress/v4_t_bf866856-df0.redacted.json`
- v3 lessons: `docs/LESSONS_LEARNED.md` (A–BJ). Lessons AB / AC / AD /
  AE / AM / AT survive as legacy/adapter-only per v3-only appendix.
- v3 prior code (will move to `legacy/v3-bridge/` post-Phase 5):
  `skill/scripts/bridge.mjs`, `skill/scripts/launcher.mjs`,
  `skill/scripts/clawroomctl.mjs`.

## 2026 Best-Practice Anchors

- [Anthropic Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
  — skills as on-demand filesystem workflows + scripts, not specialized
  agents per task.
- [Anthropic 2026 skills blog](https://claude.com/blog/building-agents-with-skills-equipping-agents-for-specialized-work)
  — equip existing agents for specialized work via skills, not by
  spawning new agents.
- [Cloudflare Durable Objects + WebSockets](https://developers.cloudflare.com/durable-objects/best-practices/websockets/)
  — DOs are the right primitive for persistent room/state/wakeup
  coordination.
