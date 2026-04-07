# Archive: Pre-v2 cleanup (2026-04-08)

Everything in this directory is the **previous Python/FastAPI architecture** of ClawRoom. It is no longer wired to any deployment, no longer tested, and no longer imported by any canonical code path. It is preserved here for historical reference only.

## What's in here

| Path | What it was |
|---|---|
| `apps/api/` | FastAPI service (`roombridge_api`, `clawroom_api`). Never deployed. |
| `apps/runnerd/` | Python sidecar that managed background runner attempts |
| `apps/codex-bridge/` | CLI bridge between the room API and Codex agents |
| `apps/openclaw-bridge/` | CLI bridge between the room API and OpenClaw agents |
| `packages/client/` | Python client library (`clawroom_client_core`) |
| `packages/core/` | Python domain models (`roombridge_core`) |
| `packages/store/` | SQLAlchemy + Alembic store layer (`roombridge_store`) |
| `docker/` | Postgres compose for the local Python dev loop |
| `tests/conformance/` | Conformance tests for the Python API |
| `skills/clawroom-lead/` | Old "lead agent" experiment that delegated to a worker agent |
| `skills/openclaw-telegram-e2e/` | Old end-to-end harness skill for Telegram |
| `scripts/` | Old experiment runners and deploy helpers |
| `reports/` | Captured output from old experiments and smoke tests |
| `docs/progress/` | Timeline / experiment logs from the previous architecture |
| `docs/decisions/` | Old ADRs |
| `docs/plans/` | Old plans (executed or abandoned) |
| `docs/proposals/` | Old proposals |
| `docs/spec/` | Old specifications |
| `docs/skills/` | Old skill publishing notes |
| `docs/context/` | Old context / positioning docs |
| `docs/ops/` | Old ops runbooks |
| `docs/fe-design/` | Old front-end design notes |
| `pyproject.toml`, `requirements.txt`, `uv.lock`, `alembic.ini` | Old Python project config |

## Why we archived instead of deleted

A few reasons:

1. **Recoverable lessons.** `docs/progress/` has root-cause experiment logs that informed the current architecture. The high-signal parts are already in `docs/LESSONS_LEARNED.md`, but the raw logs remain useful for "why did we do it this way" questions.
2. **Historical fairness.** A lot of work went into this code. Archiving (vs deleting) leaves the door open to revisit specific patterns without trawling git history.
3. **Cheap to drop later.** If after a few weeks nothing in this archive has been referenced, the entire directory can be removed in a single commit.

## What replaced it

The current architecture lives at the repository root:

- `apps/edge/` — Cloudflare Worker + Durable Objects (deployed to `api.clawroom.cc`). Replaces the FastAPI service entirely.
- `apps/monitor/` — Vite app (deployed to `clawroom.cc`). Same UI surface as before, simpler stack.
- `.agents/skills/clawroom/` — Canonical skill. Replaces all bridge CLIs and sidecars.

The single most important architectural change captured in this archive is the move from concurrent `openclaw agent` CLI calls (which silently corrupt under load — see `docs/blog/concurrent-tool-call-contamination.md`) to a direct WebSocket client (`gateway_client.py`).

## Do not import

Nothing in `archive/` should be imported by the current `apps/edge/`, `apps/monitor/`, or `.agents/skills/clawroom/` code. If you find yourself needing something from here, copy it forward into the canonical tree with intent — don't add a path dependency on the archive.
