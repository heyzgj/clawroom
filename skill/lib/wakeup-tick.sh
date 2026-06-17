#!/usr/bin/env bash
# skill/lib/wakeup-tick.sh — the ONE canonical, version-controlled wakeup tick.
#
# `clawroom arm` registers a launchd job whose ProgramArguments point at THIS
# script (never a per-room copy). One launchd run = one `heartbeat` + branch on
# its action. All per-room variation comes from EnvironmentVariables in the
# plist (room, role, skill dir, log path, label, agent script). This file holds
# zero room-specific data, so a fix here fixes every armed room at once.
#
# This is the validated pattern from references/wakeup-recipes.md Recipe B:
# node-parse (no eval), fail LOUD on empty/unparseable heartbeat output (a
# silently-dead watcher is the worst failure for an owner who walked away),
# per-room log, self-bootout on cancel.
#
# Required env (set by the plist `arm` writes):
#   CLAWROOM_ROOM, CLAWROOM_ROLE, CLAWROOM_SKILL_DIR, CLAWROOM_AGENT_SCRIPT
# Optional env:
#   CLAWROOM_LAUNCHD_LABEL (for self-bootout on cancel),
#   CLAWROOM_WAKE_LOG (defaults under the state dir),
#   CLAWROOM_STATE_DIR (inherited by the heartbeat for state lookup).
set -uo pipefail

ROOM="${CLAWROOM_ROOM:?set CLAWROOM_ROOM}"
ROLE="${CLAWROOM_ROLE:?set CLAWROOM_ROLE}"
SKILL_DIR="${CLAWROOM_SKILL_DIR:?set CLAWROOM_SKILL_DIR}"
LABEL="${CLAWROOM_LAUNCHD_LABEL:-}"
LOG="${CLAWROOM_WAKE_LOG:-${CLAWROOM_STATE_DIR:-$HOME/.clawroom-v4}/wakeup-${ROOM}-${ROLE}.log}"
AGENT_SCRIPT="${CLAWROOM_AGENT_SCRIPT:?set CLAWROOM_AGENT_SCRIPT}"

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

# TCC / bad-path guard: a launchd job cannot cwd into Desktop/Documents/Downloads
# (macOS denies it with EPERM uv_cwd). cd failure is loud, never a silent no-op.
cd "$SKILL_DIR" || {
  printf '[%s] ERROR bad CLAWROOM_SKILL_DIR=%s (cannot cd — TCC-protected or missing?)\n' "$(date +%H:%M:%S)" "$SKILL_DIR" >>"$LOG"
  exit 1
}

# The knock. A heartbeat that prints NOTHING is a MISCONFIG (node off PATH /
# TCC), not "nothing happened" — make it loud.
OUT="$(./cli/clawroom heartbeat --room "$ROOM" --role "$ROLE" 2>&1)"
[ -z "$OUT" ] && {
  printf '[%s] ERROR heartbeat empty — check node on PATH + skill not under Desktop\n' "$(date +%H:%M:%S)" >>"$LOG"
  exit 1
}

# Parse the action with node (guaranteed on PATH — the CLI needs it). FAIL LOUD
# on unparseable output; never silently fall through to noop.
ACTION="$(printf '%s' "$OUT" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(String(JSON.parse(s).action||""))}catch(e){process.exit(7)}})')" \
  || {
    printf '[%s] ERROR unparseable heartbeat output: %s\n' "$(date +%H:%M:%S)" "$OUT" >>"$LOG"
    exit 1
  }

printf '[%s] %s\n' "$(date +%H:%M:%S)" "$OUT" >>"$LOG"

case "$ACTION" in
  wake_agent)
    printf '[%s] >>> waking agent\n' "$(date +%H:%M:%S)" >>"$LOG"
    bash "$AGENT_SCRIPT" >>"$LOG" 2>&1 \
      || printf '[%s] agent invoke failed\n' "$(date +%H:%M:%S)" >>"$LOG"
    ;;
  notify_owner)
    osascript -e 'display notification "Your ClawRoom agent needs your decision — open the session to answer." with title "ClawRoom"' 2>/dev/null || true
    ;;
  cancel)
    osascript -e 'display notification "ClawRoom room finished." with title "ClawRoom"' 2>/dev/null || true
    # Self-disarm: the room is over (mutual close or TTL), so boot out our own job.
    [ -n "$LABEL" ] && launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
    ;;
  noop)
    : ;;
  *)
    printf '[%s] heartbeat action=%s — no-op this tick\n' "$(date +%H:%M:%S)" "$ACTION" >>"$LOG"
    ;;
esac
