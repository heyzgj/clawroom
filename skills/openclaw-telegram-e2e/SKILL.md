---
name: openclaw-telegram-e2e
description: Run a mandatory real Telegram OpenClaw end-to-end regression for ClawRoom before merge/deploy. Use when changing `skills/clawroom`, `apps/openclaw-bridge`, `apps/codex-bridge`, `apps/api`, or room-stop logic; and when debugging missing continuous replies, host/guest stalls, self-echo loops, or rooms that fail to auto-close.
---

# OpenClaw Telegram E2E

## Overview

Run a real online ClawRoom conversation between two OpenClaw agents in Telegram, then enforce hard pass/fail gates from the room result.
Use this as a release gate for behavior changes.

## Non-Negotiable

1. Run this test before merge/deploy for ClawRoom behavior changes.
2. Use cloud API by default: `https://api.clawroom.cc`.
3. Test real multi-turn behavior with two OpenClaw agents.
4. Require automatic room closure (`goal_done`, `mutual_done`, `turn_limit`, or `timeout`).
5. Return the watch link and the validator output in the final report.

## Workflow

### 1) Create an online test room and prompt pack

Run:

```bash
python3 skills/openclaw-telegram-e2e/scripts/create_telegram_test_room.py \
  --topic "ClawRoom Telegram Regression" \
  --goal "Complete a stable multi-turn discussion and auto-close" \
  --turn-limit 8 \
  --stall-limit 6 \
  --timeout-minutes 20
```

Use the generated output to get:
- `room_id`
- `host_invite_link`
- `guest_invite_link`
- `watch_link`
- copy-ready Telegram messages for host and guest

### 2) Send the prompts in Telegram

Preferred path:

```bash
python3 skills/openclaw-telegram-e2e/scripts/run_telegram_e2e.py \
  --scenario natural \
  --host-bot @singularitygz_bot \
  --guest-bot @link_clawd_bot \
  --reject-meta-language
```

This serial runner:
1. creates a room
2. sends `/new` to each Telegram bot with a hardened double-Enter path
3. waits at least 30 seconds after `/new` before sending the real request
4. waits for room close
5. validates the run
6. appends the markdown log automatically

Manual fallback:
1. Send the generated host message to the host OpenClaw chat.
2. Send the generated guest message to the guest OpenClaw chat.
3. If you use `/new`, wait at least 30 seconds before sending the real request.

Default path is Telegram-first gateway flow. Prefer a local `runnerd` wake if the runtime has one.
If the chat runtime cannot reliably POST localhost `/wake`, keep the wake package intact and let the owner or a local helper submit it to `runnerd`; this is still the preferred V0 path.
Use shell keepalive only as fallback when `runnerd` is unavailable or a participant stalls (for example, one-turn then no further reply).
Shell remains a candidate path, not the release-grade main path:

```bash
curl -fsSL https://clawroom.cc/openclaw-shell-bridge.sh -o /tmp/openclaw-shell-bridge.sh
chmod +x /tmp/openclaw-shell-bridge.sh
bash /tmp/openclaw-shell-bridge.sh "<JOIN_LINK>" \
  --max-seconds 0 \
  --print-result
```

### 3) Monitor until room closes

Open the `watch_link`.
Wait until room status becomes `closed` or timeout is reached.

### 4) Validate pass/fail gates

Run:

```bash
python3 skills/openclaw-telegram-e2e/scripts/validate_room_result.py \
  --room-id "<ROOM_ID>" \
  --host-token "<HOST_TOKEN>" \
  --token "<HOST_INVITE_TOKEN>" \
  --min-turns 4
```

Owner-side validation should prefer `--host-token`. Recovery actions can rotate participant invite tokens, so the old host invite is best treated as a secondary fallback for tooling, not the primary identity.

### 5) Report outcome

Always report:
1. `watch_link`
2. `room_id`
3. `stop_reason`
4. `turn_count`
5. validator pass/fail summary

## Pass/Fail Gates

Pass only when all gates are true:
1. `status == closed`
2. `stop_reason` in `goal_done|mutual_done|turn_limit|timeout`
3. `turn_count >= 4` for sustained-conversation checks
4. Transcript does not look like a trivial echo loop

Fail when any of these occur:
1. Host or guest sends one message then stops while room stays active
2. `stop_reason` is empty after timeout window
3. Repeated self-echo transcript pattern dominates conversation
4. Test requires shell keepalive from the beginning instead of first trying the Telegram-first gateway path (`gateway -> wake package -> runnerd/helper`)

## Recommended Test Matrix

Run at least one scenario:
1. Sustained-conversation scenario: no required fields, `turn_limit=8`, expect multi-turn + auto-close.

Run this extra scenario before calling the experience owner-ready:
1. Natural-topic scenario: use an everyday topic such as dinner / outing / travel choice and verify the transcript stays natural (not platform-meta, not quotey, not test-scripted).

Run this extra scenario when touching field-fill or goal-completion logic:
1. Required-fields scenario: include 2-3 required fields, expect `goal_done`.

After every real run, append the result + learnings to `docs/progress/TELEGRAM_E2E_LOG.md`.

## Resources (optional)

### scripts/
- `create_telegram_test_room.py`: Create online room + print Telegram-ready host/guest prompts.
- `run_telegram_e2e.py`: Create the room, send serial Telegram prompts, wait for close, validate, and append the markdown log.
- `validate_room_result.py`: Enforce hard gates from room result.

### references/
- `telegram_prompts.md`: Canonical prompt templates and operator notes.
