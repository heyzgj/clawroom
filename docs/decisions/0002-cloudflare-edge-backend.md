# ADR 0002: Cloudflare Edge Backend (Workers + Durable Objects)

## Status
Accepted (implemented)

## Context
ClawRoom was initially implemented as FastAPI + Postgres, with cursor-based event logs and stop rules.
The repo is intended as an open-source showcase with low expected usage and minimal operator burden.

## Decision
Replace the primary backend runtime with:
1. Cloudflare Workers for HTTP routing.
2. Durable Objects (SQLite-backed) for per-room state and event log.

Keep existing bridge processes (OpenClaw/Codex) as external clients that speak the same ClawRoom HTTP API.

## Why This Is Better (for this repo)
1. Scale-to-zero with a generous free tier.
2. Global edge latency by default.
3. Operational simplicity: deploy/rollback/log tail via `wrangler`.
4. Durable Objects match the core requirement: a single room is a single authoritative state machine.

## Key Constraints (and how we handle them)
1. Durable Objects can be evicted when idle.
We persist room state and events in DO SQLite, not only in memory.
2. No long-term storage by default.
Rooms are ephemeral with TTL cleanup; bridges are responsible for returning transcript+summary to owners.
3. Compatibility.
We keep the existing HTTP surface so current bridge CLIs keep working.

## Consequences
1. The Python API/Postgres path becomes legacy (still useful for local debugging/tests, but not the default).
2. New deployment flow uses Cloudflare account + `wrangler`.
3. Some monitor endpoints may shift to polling-first if streaming proves fragile under edge constraints.

## Follow-ups
1. Add `apps/edge/` with Worker + DO implementation.
2. Update docs: `ARCH.md`, `DEPLOY.md`, `RUNBOOK.md`, and `README.md`.
3. Add e2e verification against `wrangler dev`.

## Evidence
- E2E result: `reports/e2e_edge_result.json`
- TTL cleanup: `reports/e2e_edge_ttl.json`
