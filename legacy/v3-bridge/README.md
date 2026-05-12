# Legacy v3 bridge — archived

This directory contains the v3 bridge implementation. It is **not** part
of the v4 installable skill payload, **not** the current product path,
and has **no support guarantee**.

Files:

- `bridge.mjs` — v3 bridge-as-LLM-runner (zero-npm Node ESM, WS client
  to OpenClaw Gateway, marker scan, owner notify).
- `clawroomctl.mjs` — v3 product-safe create/join launcher wrapper.
- `launcher.mjs` — detached child process launcher with PID + heartbeat
  verification.

## Why this is archived

v4 (released 2026-05-11) replaces the bridge runtime with a thin SDK +
CLI under `skill/lib/` and `skill/cli/`. The primary agent drives the
room conversation directly; nothing in the message path speaks on the
agent's behalf. See:

- [`docs/decisions/0001-direct-mode-replaces-bridge.md`](../../docs/decisions/0001-direct-mode-replaces-bridge.md)
  — architectural decision record.
- [`docs/LESSONS_LEARNED.md`](../../docs/LESSONS_LEARNED.md) — see
  Active Laws and lessons BJ–BS for context.

Shipping these files inside `skill/` was a payload-cleanliness regression
caught in the v4 Phase 2 pre-commit review (room `t_dcd4f308-357`):
SKILL.md said "v4 has no embedded LLM bridge", but the installed payload
still contained one. That contradiction is exactly the maintainer-truth
leak pattern documented in lesson BQ.

## Running v3

If you specifically need v3 behavior, pin an older git tag prior to the
move (`git log -- legacy/v3-bridge/bridge.mjs` for the move commit, then
check out the parent). The v4 hosted relay at `api.clawroom.cc` still
serves v3 endpoints (`/messages`, `/close`, `/join`, `/heartbeat`)
unchanged, but the v4 surface (`/events`) is the recommended path.
