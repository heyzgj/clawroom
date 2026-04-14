# Migration Record — 2026-04-15

This repo (`clawroom/`, previously `clawroom-v3/`) is now canonical. The older
`agent-chat/` repo is no longer actively developed against.

## Why

- No production users — DNS / GitHub rename can happen on our timeline, not
  forced by a migration window.
- `agent-chat/apps/edge/` carried ~455 KB of v2 thick-protocol TypeScript
  (required-fields state machine, intent parsing, continuation hints) that
  the v3.1 thin-relay architecture makes obsolete. Keeping it around was
  complexity drag, not IP.
- v3.1 is now the working direction (first real Telegram E2E passed on
  2026-04-14, room `t_92615621-4a8`).

## Source of truth

- Source repo: `~/Desktop/project/agent-chat/` (local) and
  `https://github.com/heyzgj/clawroom` (remote, currently still tracking the
  pre-migration tree — will be renamed to `heyzgj/clawroom-v2-archive` once
  v3.1 validation is complete, see "Deferred" below).
- Last commit on source side before this migration: `a0c3616`
  ("docs(lessons): land v3.1 E2E evidence + preemptive marker-scan lesson").

## What moved over (2026-04-15)

| Source (agent-chat) | Destination (clawroom) | Notes |
|---|---|---|
| `docs/LESSONS_LEARNED.md` | `docs/LESSONS_LEARNED.md` | Full catalog. Part 7 (v3.1 DO relay + verified bridge + Telegram E2E) and Lessons Z–AI land intact. Historical paths inside (e.g. `~/Desktop/project/clawroom-v3/`) are preserved as-written; they describe the state at E2E time. |
| `docs/blog/concurrent-tool-call-contamination.md` | `docs/blog/` | Public-facing writeup of the `openclaw agent` CLI contamination discovery. Still relevant — the v3 bridge uses the WS client specifically because of this. |
| `docs/progress/TELEGRAM_E2E_LOG_2026_04_08.md` | `docs/progress/` | 22 KB real-bot E2E log — S1-REAL term sheet, A1/A2/D1/B1/C1 runs, KK × clawd × Link cross-runtime findings. The record that drove v2.2.0 → v2.2.1 skill fixes. |
| `docs/progress/v3_1_t_92615621-4a8.redacted.json` | `docs/progress/` | Evidence artifact for Part 7. Tokens redacted, thread closed. |
| `docs/clawroom_design.md` | `docs/design/landing-design.md` | Landing page design context. Mockup path reference inside was updated to point to the co-migrated mockup. |
| `apps/monitor/public/landing-v2/index.html` | `docs/design/landing-mockup.html` | The locked Space Mono / IBM Plex / pure-black landing mockup, so the design doc stays self-contained. |

Byte-level parity of all migrated files was verified with `cmp -s` before this
commit.

## What did NOT move over (intentionally)

Everything below is considered obsolete or not-ongoing. Git history at the
`agent-chat` remote preserves it if we ever need to refer back.

- `apps/edge/src/*.ts` — v2 thick-protocol worker (~455 KB). Replaced by
  `clawroom/relay/worker.ts`. v3 keeps the server semantic-free.
- `apps/monitor/` — v2 state-machine UI, keyed to required-fields semantics
  that no longer exist in v3. A v3 monitor will be a clean rewrite when
  needed.
- `.agents/skills/clawroom/` (scripts + v2.2.x SKILL.md) — v2-era poller,
  gateway client, owner-context writers. Replaced by `clawroom/bridge.mjs`
  and `clawroom/SKILL.md`.
- `archive/2026-04-08-pre-v2-cleanup/` (~8.6 MB Python stack) — pre-v2
  architecture. Already cold-storage.
- `archive/2026-04-03-clawroom-skill-repo/` — the even older skill-only
  clone we archived on 2026-04-11. Also cold-storage.
- `CLAUDE.md`, `README.md`, `INSTALL_SKILL.md` — rewritten fresh here, not
  copied. The old versions described v2 architecture and would have been
  misleading.

## Transitional files in this tree (not in initial commit)

- `daemon.mjs`, `daemon.py` — earlier iterations of what became `bridge.mjs`.
  Kept on disk untracked in case the iteration history is useful later.
- `experiment/phase1.sh` — one-off experiment harness.

If any of these turn out to be needed, add them in a follow-up commit with
their own message — don't retroactively rewrite the initial commit.

## Deferred (tied to T3 validation, not to this migration)

The following are NOT done yet, and should only happen after v3.1 clears the
next validation bar (ASK_OWNER round-trip E2E + one S1-class multi-turn
negotiation):

1. Rename GitHub repo: `heyzgj/clawroom` → `heyzgj/clawroom-v2-archive`;
   push this tree as the new `heyzgj/clawroom`.
2. Re-point Cloudflare DNS: `api.clawroom.cc` → the v3.1 relay Worker
   (currently served by the v2 worker in `agent-chat/apps/edge/`).
3. Re-point Cloudflare Pages: `clawroom.cc` landing → whatever v3 landing
   is built.
4. Re-point `clawroom.cc/skill.md` → the v3 `SKILL.md` in this repo (once
   the v3 skill is final).

Until those happen, `agent-chat` remains the live-serving repo for the
`.cc` domains even though all new development happens here.

## Safety net

If v3.1 hits a fundamental blocker (e.g. T3 proves unworkable on OpenClaw),
we can roll back:

- `agent-chat/` folder is preserved at `~/Desktop/project/agent-chat/`
  (local) and `heyzgj/clawroom` (remote, still live).
- Domain config is unchanged, so `api.clawroom.cc` keeps serving v2.
- This migration is additive — it didn't delete anything from agent-chat.

## For future sessions

If you (future Claude / codex / another agent) are reading this and looking
for something that seems missing from `clawroom/`, check the list above. If
it's in the "did NOT move over" section, it's in `agent-chat/` on purpose.
If it's in "what moved over" and you can't find it, something was deleted
that shouldn't have been — look at the git history here to find out when
and why.
