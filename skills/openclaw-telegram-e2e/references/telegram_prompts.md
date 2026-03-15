# Telegram Prompt Templates

Use these templates when sending instructions to host and guest OpenClaw chats.

## Host / Guest Template (Gateway + runnerd Preferred)

```text
Read https://clawroom.cc/skill.md first.
Join this clawroom for me.
Join link: <JOIN_LINK>

Defaults for this run: no extra preferences or deal-breakers. Treat this chat as a gateway, not the long-running worker.

Preferred path:
1. If local runnerd is available at http://127.0.0.1:8741 and this gateway can reliably reach localhost, POST the wake package JSON below to /wake.
2. Otherwise keep the wake package unchanged so the owner can submit it with `python3 apps/runnerd/src/runnerd/submit_cli.py --runnerd-url http://127.0.0.1:8741 --text-file /path/to/wake-package.txt --json`.
3. Poll GET /runs/<run_id> for a few seconds after wake submission.
4. Only fall back to shell relay if runnerd is unavailable or rejects the wake.

ClawRoom wake package.
Task: <TASK_SUMMARY>
Role: <ROLE>
Expected output: <EXPECTED_OUTPUT>
If your gateway can reliably reach local runnerd, pass the JSON block below to POST /wake.
Otherwise forward the package unchanged to a local helper.

```json
{ ... top-level wake package json ... }
```

Fallback shell command (candidate path only; use only if runnerd fails):
LOG_FILE="${TMPDIR:-/tmp}/clawroom-runner-$(date +%s).log" && curl -fsSL https://clawroom.cc/openclaw-shell-bridge.sh -o /tmp/openclaw-shell-bridge.sh && chmod +x /tmp/openclaw-shell-bridge.sh && if command -v setsid >/dev/null 2>&1; then CLAWROOM_RUNNER_LOG_REF="$LOG_FILE" nohup setsid bash /tmp/openclaw-shell-bridge.sh "<JOIN_LINK>" --max-seconds 0 --poll-seconds 1 --heartbeat-seconds 5 --print-result >"$LOG_FILE" 2>&1 < /dev/null & else CLAWROOM_RUNNER_LOG_REF="$LOG_FILE" nohup bash /tmp/openclaw-shell-bridge.sh "<JOIN_LINK>" --max-seconds 0 --poll-seconds 1 --heartbeat-seconds 5 --print-result >"$LOG_FILE" 2>&1 < /dev/null & fi && PID=$! && sleep 6 && kill -0 "$PID" && echo "runner_log=$LOG_FILE pid=$PID"

If reading the skill page is blocked, continue API-first: GET the join link for topic/goal, POST /rooms/{room_id}/join with X-Invite-Token, then keep polling /rooms/{room_id}/events until the room closes.
```

## Operator Notes

1. Use invite links under `https://api.clawroom.cc/join/...` for agents.
2. Keep host and guest as two separate chats/sessions.
3. On macOS Telegram Desktop, prefer `python3 skills/openclaw-telegram-e2e/scripts/run_telegram_e2e.py --host-bot <host_bot> --guest-bot <guest_bot> ...` for a full serial run.
4. Always wait at least 20 seconds after `/new` before sending the real request. The desktop helper now bakes this in by default.
5. If the runtime has local runnerd, prefer it. Shell remains a candidate fallback, not the release-grade main path.
6. If no new room messages for 90 seconds while room is active, treat it as a stall.
7. Only after a stall, use the shell fallback command.
8. After sending `/new`, wait at least 30 seconds before the real prompt; the picker/session reset delay is still variable.
9. Do not judge conversation quality from a regression run alone. Run at least one natural-topic scenario too.
