# ClawRoom v3.1 Implementation and E2E Report

Date: 2026-04-14

## Executive Summary

The original ClawRoom v3 direction was correct at the product level: two owners' OpenClaw agents enter one room, negotiate through a relay, and notify each owner when done. The implementation needed one important correction: v3.1 should not be a pure KV relay with hopeful `nohup` launches. It should be a Durable Object relay plus a verified bridge runtime.

The first real Telegram E2E passed with:

- local clawd Telegram OpenClaw as host,
- Railway-hosted Link Telegram OpenClaw as guest,
- Cloudflare Durable Object relay,
- both OpenClaw runtimes self-launching the bridge from Telegram prompts,
- mutual close,
- both owner Telegram notifications delivered.

Passing room:

```json
{
  "room_id": "t_92615621-4a8",
  "stop_reason": "mutual_close",
  "turn_count": 4,
  "message_count": 2,
  "close_count": 2,
  "roles": "host -> guest -> host -> guest"
}
```

Artifact:

`/Users/supergeorge/.clawroom-v3/e2e/t_92615621-4a8.json`

## Original Proposal

The proposed architecture was:

1. Relay: Cloudflare Worker with GET-friendly API.
2. Bridge: zero-npm Node script, no LLM inside; transport only.
3. OpenClaw Gateway: local WebSocket at `ws://localhost:18789`.
4. Skill: download/check bridge, launch it in background, return immediately.
5. Notification: direct Telegram Bot API, not OpenClaw `deliver`.

This stayed intact conceptually. The biggest implementation shift was replacing "KV queue" with "Durable Object room core" and replacing "nohup launch" with "verified launcher".

## What Changed from Proposal to v3.1

| Area | Initial idea | v3.1 result |
| --- | --- | --- |
| Relay storage | Pure KV queue | SQLite-backed Durable Object |
| Turn-taking | Desired protocol behavior | Server-enforced `409` on same-role consecutive post |
| Bridge launch | `nohup node bridge.mjs &` | `launcher.mjs` verifies PID, runtime-state, log, relay heartbeat |
| OpenClaw agent | Possible `main` reuse | Dedicated `clawroom-relay` agent |
| Session key | Room session | `agent:clawroom-relay:clawroom:<thread>:<role>` |
| Cross-machine proof | `railway run` attempt | Telegram self-launch plus Railway runtime logs |
| Notification | Direct Bot API | Implemented, requires explicit chat/owner binding |
| E2E oracle | Observe Telegram | Validate relay state, runtime state, logs, notification delivery |

## Implemented Files

- `/Users/supergeorge/Desktop/project/clawroom-v3/relay/worker.ts`
- `/Users/supergeorge/Desktop/project/clawroom-v3/relay/wrangler.toml`
- `/Users/supergeorge/Desktop/project/clawroom-v3/bridge.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/launcher.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/SKILL.md`
- `/Users/supergeorge/Desktop/project/clawroom-v3/docs/REAL_TELEGRAM_E2E.md`
- `/Users/supergeorge/Desktop/project/clawroom-v3/scripts/telegram_e2e.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/scripts/validate_e2e_artifact.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/scripts/fix_railway_clawroom_agent.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/scripts/inspect_notify_config.mjs`
- `/Users/supergeorge/Desktop/project/clawroom-v3/scripts/set_telegram_allow_from_from_sessions.mjs`

## E2E Timeline

1. Deployed the DO relay to `https://clawroom-v3-relay.heyzgj.workers.dev`.
2. Verified relay smoke behavior: create, post, same-role `409`, guest post, mutual close.
3. Created a downloadable test asset bundle for `launcher.mjs`, `bridge.mjs`, and supporting scripts.
4. Sent host and guest launch prompts through Telegram Desktop with `scripts/telegram_e2e.mjs`.
5. First E2E attempt failed at Gateway connect schema because `client.id` was wrong.
6. Fixed bridge client id to `gateway-client`; local Gateway smoke passed.
7. Second E2E attempt proved host path but Railway guest failed because `clawroom-relay` workspace pointed under `/root/.openclaw`.
8. Fixed Railway Link agent workspace to `/data/.openclaw/workspaces/clawroom-relay`.
9. Third E2E reached mutual close, but guest notification skipped because Railway had no usable Telegram chat target.
10. Recovered owner chat binding from Link OpenClaw Telegram sessions and wrote it to OpenClaw config without exposing the full chat id.
11. Fourth E2E passed completely: host/guest self-launched from Telegram, mutually closed, both runtime heartbeats stopped, both notifications delivered.

## Passing Transcript Shape

1. Host posted opening availability for Wednesday 3pm Shanghai.
2. Guest confirmed that the slot worked for Tom.
3. Host posted close summary.
4. Guest observed peer close, posted matching close, and notified owner.

## Validation Output

```json
{
  "ok": true,
  "room_id": "t_92615621-4a8",
  "stop_reason": "mutual_close",
  "turn_count": 4,
  "message_count": 2,
  "close_count": 2
}
```

Checks passed:

- `room_closed`
- `mutual_close`
- `event_count`
- `message_count`
- `close_roles`
- `turn_taking`
- `runtime_stopped`
- `summary_present`
- `not_echo_loop`

## Lessons Learned

1. Durable Objects are the correct relay core for multi-agent coordination. KV can be useful for read-heavy cache-like data, but not for concurrent room append semantics.
2. `railway run` is not a remote execution proof. It runs locally with Railway variables. Real container proof needs Railway SSH for diagnostics or Telegram-triggered self-launch for product E2E.
3. SSH is not product path. It is a diagnostic scalpel for inspecting the actual container.
4. OpenClaw state directories are runtime-specific. Railway Link uses `/data/.openclaw`; code must respect `OPENCLAW_STATE_DIR`.
5. Dedicated `clawroom-relay` agent isolation is necessary. Do not run bridge traffic through `main`.
6. `nohup ... &` is not enough. The launcher must verify PID, runtime-state, relay heartbeat, and logs before reporting success.
7. Gateway schema is strict. A local Gateway handshake smoke test should happen before any Telegram E2E.
8. Direct Telegram notification needs an explicit owner binding. Bot token alone is insufficient; `sendMessage` requires `chat_id`.
9. Telegram UX can look successful while the room state is wrong. The release gate must validate relay state and runtime state.
10. Downloadable assets are a good integration bridge, but production should move to bundled or signed/hash-pinned skill assets.

## Current State

Working:

- DO relay deployed.
- Node bridge works locally and in Railway.
- Local clawd and Railway Link can self-launch through Telegram prompts.
- Both sides can negotiate, mutually close, and notify owners.
- E2E harness and validator are reproducible.

Still to harden:

- Replace temporary downloadable test bundle with production skill packaging or signed asset manifest.
- Make notify target binding a first-class product contract instead of a one-off config repair.
- Decide whether v3.1 should merge into the existing `agent-chat` DO room core or stay in `clawroom-v3` as the isolated hardening branch first.
- Add automated deploy smoke for relay turn gate and mutual-close behavior.
