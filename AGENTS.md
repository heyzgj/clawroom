# AGENTS.md — ClawRoom v3.1

Read `CLAUDE.md` and `docs/LESSONS_LEARNED.md` before changing relay,
bridge, launcher, skill, Telegram routing, or E2E code.

## Mandatory ClawRoom Preflight

Before starting any ClawRoom work, especially before running E2E or launching
bridges, check for leftover local bridge/runtime processes:

```sh
ps -axo pid,etime,command | rg 'bridge\.mjs --thread|clawroom-v3' || true
```

If any process is found, decide whether it belongs to the current active run.
Kill stale processes before continuing:

```sh
ps -axo pid,etime,command | awk '/bridge\.mjs --thread/ && !/awk/ {print $1}' | xargs -r kill
```

Then verify the list is empty. Do not start a new E2E while old bridge
processes are still polling the relay.

Why this matters: on 2026-04-17 stale local bridges with fake/expired room
ids kept polling invalid relay rooms and burned Cloudflare Durable Objects free
tier quota. See `docs/LESSONS_LEARNED.md` Lesson AQ.

## After Every E2E

- Confirm both runtime states are `stopped` or intentionally `failed`.
- Re-run the process sweep above.
- Preserve the E2E artifact, redacted transcript, validator result, and any
  failure evidence under `docs/progress/`.
- Update `docs/LESSONS_LEARNED.md` when the run teaches a new failure mode or
  product constraint.
