# AGENTS.md — ClawRoom v3.1

Read `CLAUDE.md` and `docs/LESSONS_LEARNED.md` before changing relay,
bridge, launcher, skill, Telegram routing, or E2E code.

## Mandatory ClawRoom Preflight

Before starting any ClawRoom work, especially before running E2E or launching
bridges, check for leftover local bridge/runtime processes:

```sh
pgrep -f '^node .*bridge\.mjs --thread' || true
```

If any process is found, decide whether it belongs to the current active run.
Kill stale processes before continuing:

```sh
kill <pid>
```

Then verify the list is empty. Do not start a new E2E while old bridge
processes are still polling the relay.

Why this matters: on 2026-04-17 stale local bridges with fake/expired room
ids kept polling invalid relay rooms and burned Cloudflare Durable Objects free
tier quota. See `docs/LESSONS_LEARNED.md` Lesson AQ.

For cross-machine Telegram E2E, also check the Railway Link container itself:

```sh
railway status
railway ssh sh -lc "ps -eo pid,args | grep 'bridge.mjs --thread' | grep -v grep | awk '{print \$1}'"
```

The command should print only live bridge PIDs. Historical `[node] <defunct>`
zombie rows are not live relay pollers; record them if they grow, but do not
mistake them for an active room bridge.

The detailed future-agent procedure is in
`docs/runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`.

Do not paste full local or Railway process command lines into notes, artifacts,
or chat. Bridge argv can contain room tokens and owner chat ids. Use PID-only
checks, then inspect runtime-state and bridge log files by room id when you need
context.

Do not run or paste raw `railway ssh env`; Railway env can contain Telegram
bot tokens, gateway tokens, passwords, and API keys. Query specific keys with
`railway ssh printenv KEY` when needed.

## Skill Update Rule

If `SKILL.md`, `bridge.mjs`, `launcher.mjs`, or `clawroomctl.mjs` changes,
do not run E2E against whatever OpenClaw already has installed. First remove or
overwrite the visible skill bundle in both runtimes, then verify the current
repo files are installed:

- local clawd: `~/clawd/skills/clawroom-v3`
- Railway Link: `/data/workspace/skills/clawroom-v3`

Run `openclaw skills info clawroom-v3` locally and
`OPENCLAW_STATE_DIR=/data/.openclaw openclaw skills info clawroom-v3` on
Railway before starting the next product-path room.

## Runtime Source Boundary

Do not treat sibling source checkouts such as
`/Users/supergeorge/Desktop/project/openclaw` or
`/Users/supergeorge/Desktop/project/clawdbot` as ClawRoom dependencies. They
may be useful for optional adapter research, but ClawRoom public behavior must
ship from this repo: relay, bridge, launcher, `clawroomctl.mjs`, skill, BYO
relay skill, validator, and artifacts.

The portable ASK_OWNER path is the relay-owned owner decision URL. Do not make
public readiness depend on a local OpenClaw or bot source patch unless the user
explicitly asks to test an optional deployment-specific adapter.

## Product UX Boundary

ClawRoom should hide its own technical details by default: no launcher JSON,
PIDs, runtime/log paths, hashes, bearer tokens, create keys, or debug labels in
owner-facing Telegram messages. OpenClaw's own greeting/persona chatter is not
a ClawRoom blocker unless it prevents the skill from launching or leaks
ClawRoom internals.

Do not hard-code language policy in ClawRoom. OpenClaw should naturally follow
the owner's language; ClawRoom only checks for technical leakage and protocol
correctness.

## After Every E2E

- Confirm both runtime states are `stopped` or intentionally `failed`.
- Re-run the process sweep above.
- Preserve the E2E artifact, redacted transcript, validator result, and any
  failure evidence under `docs/progress/`.
- Update `docs/LESSONS_LEARNED.md` when the run teaches a new failure mode or
  product constraint.
