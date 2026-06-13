#!/usr/bin/env bash
# evals/e2e/run-e2e.sh — fully-automated cross-owner E2E with full recording.
#
# The four guarantees the owner asked for, each enforced mechanically:
#   1. No stale clawroom — scrub.sh runs + verifies clean before turn 1.
#   2. Zero clawroom memory at start — every agent turn is a COLD process
#      (codex exec / claude -p, no --resume) for turn 1; later turns resume
#      ONLY that agent's own session (a real user's assistant remembers the
#      conversation) — never any test/clawroom priming from us.
#   3. IQ-50 owner voice — prompts come verbatim from scenarios/*.txt. The
#      ONLY technical content is the ship block the friend literally pastes.
#      We add ZERO connection mechanics. Between-turn nudges are plain owner
#      voice ("有进展吗？该回就回").
#   4. Everything recorded — every turn's stdout/stderr + every room export
#      snapshot lands in runs/<ts>/ with an index, for later lesson mining.
#
# Human relay is SIMULATED faithfully: we extract the invite block the host
# agent surfaced to its owner and feed it to the guest as "朋友发我这个" —
# exactly what a human copy-pastes. We never hand the guest a token or tell
# it how to connect.
#
# Usage:
#   run-e2e.sh <scenario>            # e.g. 01-sync  (default both sides codex)
#   HOST_DRIVER=codex GUEST_DRIVER=claude run-e2e.sh 02-escalation
#
# Drivers: codex (gpt-5.5 high) | claude (opus xhigh). claude needs a logged-in
# CLI; if it 401s the turn is recorded as a failure and the run stops clean.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
SCEN="${1:?usage: run-e2e.sh <scenario-name>}"
HOST_DRIVER="${HOST_DRIVER:-codex}"
GUEST_DRIVER="${GUEST_DRIVER:-codex}"
RELAY="https://api.clawroom.cc"
MAX_TURNS_PER_SIDE="${MAX_TURNS_PER_SIDE:-6}"
ADMIN_KEY="$(grep -h CLAWROOM_ADMIN_KEY "$REPO/docs/operator-admin-key.local.txt" 2>/dev/null | cut -d= -f2)"

TS="$(date +%Y%m%d-%H%M%S)"
RUN="$HERE/runs/${SCEN}-${TS}"
mkdir -p "$RUN/host" "$RUN/guest" "$RUN/snapshots"
echo "RUN: $RUN"

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN/index.log"; }

# ---- snapshot the room via operator export (recording, requirement 4) ----
snap() { # snap <thread> <label>
  [ -z "${1:-}" ] && return 0
  curl -s -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads/$1/export" \
    > "$RUN/snapshots/$2.json" 2>/dev/null || true
}
room_state() { # room_state <thread> -> "closed host_closed guest_closed nmsgs lastrole"
  curl -s -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads/$1/export" 2>/dev/null | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); t=d['thread']; m=d['messages']
  last=m[-1]['role'] if m else 'none'
  print(t.get('closed',0),t.get('host_closed',0),t.get('guest_closed',0),len(m),last)
except: print('0 0 0 0 none')
"
}

# ---- per-side isolated agent config (req 2: neutral fresh-Codex baseline) ----
# We do NOT run on the maintainer's real ~/.codex — that machine has personal
# MCP servers (Supabase etc.), a personal AGENTS.md, and memories, none of
# which a fresh user has and one of which (Supabase MCP) actively injected a
# fatal error into a turn. We give each side a clean CODEX_HOME containing
# ONLY the auth token (keeps login) so config.toml/MCP/AGENTS/memories are all
# absent → the closest thing to a brand-new user who just installed Codex.
# Residual: codex's built-in managed connectors are identical for every user
# (a constant, not a per-machine confound). Documented in README.
setup_iso() { # setup_iso <side>  (idempotent per run; persists across that side's turns for resume continuity)
  local side="$1" ch="$RUN/$side/codex-home"
  if [ ! -f "$ch/auth.json" ]; then
    mkdir -p "$ch"
    [ -f "$HOME/.codex/auth.json" ] && cp "$HOME/.codex/auth.json" "$ch/auth.json"
  fi
  echo "$ch"
}

# ---- one cold/resumed agent turn, fully logged ----
# fire <side> <driver> <turn-no> <prompt-file-or-string> [resume]
fire() {
  local side="$1" driver="$2" n="$3" promptsrc="$4" mode="${5:-cold}"
  local wd="$RUN/$side/work"; mkdir -p "$wd"
  local CH; CH="$(setup_iso "$side")"
  local out="$RUN/$side/turn${n}.log"
  local prompt; prompt="$(cat "$promptsrc" 2>/dev/null || printf '%s' "$promptsrc")"
  log "FIRE $side/$driver turn$n ($mode)"
  if [ "$driver" = "codex" ]; then
    # bash 3.2 (macOS) + set -u: expanding an empty array errors, so branch
    # rather than splat a possibly-empty resume_args.
    if [ "$mode" = "resume" ]; then
      ( cd "$wd" && CODEX_HOME="$CH" codex exec -m gpt-5.5 -c model_reasoning_effort=high \
          --ignore-user-config \
          -s workspace-write -c sandbox_workspace_write.network_access=true \
          -c approval_policy=never --skip-git-repo-check \
          --add-dir "$HOME/.npm" --add-dir "$HOME/.clawroom-v4" \
          resume --last "$prompt" ) > "$out" 2>&1
    else
      ( cd "$wd" && CODEX_HOME="$CH" codex exec -m gpt-5.5 -c model_reasoning_effort=high \
          --ignore-user-config \
          -s workspace-write -c sandbox_workspace_write.network_access=true \
          -c approval_policy=never --skip-git-repo-check \
          --add-dir "$HOME/.npm" --add-dir "$HOME/.clawroom-v4" \
          "$prompt" ) > "$out" 2>&1
    fi
  elif [ "$driver" = "claude" ]; then
    if [ "$mode" = "resume" ]; then
      ( cd "$wd" && claude -p "$prompt" --model opus --effort xhigh \
          --permission-mode acceptEdits \
          --allowedTools "Bash,Read,Write,WebFetch,Glob,Grep" --continue ) > "$out" 2>&1
    else
      ( cd "$wd" && claude -p "$prompt" --model opus --effort xhigh \
          --permission-mode acceptEdits \
          --allowedTools "Bash,Read,Write,WebFetch,Glob,Grep" ) > "$out" 2>&1
    fi
  fi
  log "  exit $? — $(wc -l < "$out") lines -> $out"
}

# ---- extract what the host surfaced to forward (= the human's copy) ----
# A real human forwards the WHOLE block the agent gave them (the dual-audience
# public_message: human line + pointer instructions + install + invite), not a
# hand-cleaned URL. We reconstruct that block from the host turn-1 log so the
# guest sees the actual shipped artifact. Fallback to the bare URL only if no
# block is recoverable (records which path was used).
extract_forward() {
  python3 - "$RUN/host/turn1.log" "$RELAY" <<'PY'
import re,sys
log=open(sys.argv[1],errors='ignore').read(); relay=sys.argv[2]
# The shipped forward block runs from the "给对方的一句话/One line" lead to
# the "--- end ---" marker. Grab it verbatim if present.
m=re.search(r'(给对方的一句话.*?--- end ---)', log, re.S)
if m:
    print(m.group(1).strip()); sys.exit(0)
# Fallback: bare invite URL (worst-case human forward).
u=re.search(re.escape(relay)+r'/i/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+', log)
print(u.group(0) if u else '')
PY
}
# Only accept a room created AFTER this run began — a stale room from a
# prior run must never be mistaken for the one the host just made.
newest_thread() { # newest_thread <since_epoch_ms>
  curl -s -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads" 2>/dev/null | python3 -c "
import sys,json
since=int('${1:-0}')
try:
  for t in json.load(sys.stdin)['threads']:
    if int(t.get('created_at',0)) >= since: print(t['thread_id']); break
  else: print('')
except: print('')
"
}

# ============================ RUN ============================
log "=== scrub: enforce clean room (req 1 + 2) ==="
bash "$HERE/scrub.sh" 2>&1 | tee -a "$RUN/index.log"
[ "${PIPESTATUS[0]}" -ne 0 ] && { log "ABORT: environment not clean"; exit 1; }

RUN_START_MS="$(python3 -c 'import time;print(int(time.time()*1000))')"
log "=== HOST turn 1 (cold) — scenario $SCEN (run window starts $RUN_START_MS) ==="
fire host "$HOST_DRIVER" 1 "$HERE/scenarios/${SCEN}.host.txt" cold

THREAD="$(newest_thread "$RUN_START_MS")"
log "detected room (created after run start): ${THREAD:-NONE}"
# Distinguish a HARNESS/PRODUCT failure honestly (F9 fix): if the host turn
# crashed (harness), say so; if it ran but created no room, that's a product
# finding. Either way RESULT: not a vague abort that blames the product.
if [ -z "$THREAD" ]; then
  if grep -qiE "unbound variable|command not found|No such file|syntax error" "$RUN/host/turn1.log"; then
    log "RESULT: FAIL(harness) — host turn1 crashed before creating a room (see host/turn1.log); NOT a product result"
  else
    log "RESULT: FAIL(product) — host agent ran but never created a room from the owner's request"
  fi
  exit 1
fi
snap "$THREAD" "01-after-host-turn1"

FWD="$(extract_forward)"
FWD_KIND="$(printf '%s' "$FWD" | grep -q 'end ---' && echo full-block || echo bare-url)"
log "simulated human relay — host surfaced: $FWD_KIND ($(printf '%s' "$FWD" | wc -c | tr -d ' ') chars)"
[ -z "$FWD" ] && { log "RESULT: FAIL(product) — host never gave its owner anything to forward"; exit 1; }

# Build guest prompt: scenario guest text with the ACTUAL forwarded block spliced in
GUEST_PROMPT="$RUN/guest/turn1.prompt.txt"
python3 - "$HERE/scenarios/${SCEN}.guest.txt" "$FWD" > "$GUEST_PROMPT" <<'PY'
import sys
tmpl=open(sys.argv[1]).read(); fwd=sys.argv[2]
print(tmpl.replace("__FORWARD_FROM_HOST__", fwd))
PY

log "=== GUEST turn 1 (cold) ==="
fire guest "$GUEST_DRIVER" 1 "$GUEST_PROMPT" cold
snap "$THREAD" "02-after-guest-turn1"

# ---- alternate to mutual close, owner-voice nudges, no technical input ----
NUDGE="有进展吗？对面那边该回就回，回完按你判断该收尾就收尾，弄完跟我说一声。"
ht=1; gt=1
for round in $(seq 1 "$MAX_TURNS_PER_SIDE"); do
  read -r closed hc gc n last <<< "$(room_state "$THREAD")"
  log "state: closed=$closed host_closed=$hc guest_closed=$gc msgs=$n last=$last"
  [ "$closed" = "1" ] && { log "=== MUTUAL CLOSE ==="; break; }
  if [ "$last" = "guest" ] || { [ "$hc" = "0" ] && [ "$last" = "none" ]; }; then
    ht=$((ht+1)); fire host "$HOST_DRIVER" "$ht" "$NUDGE" resume; snap "$THREAD" "r${round}-host"
  else
    gt=$((gt+1)); fire guest "$GUEST_DRIVER" "$gt" "$NUDGE" resume; snap "$THREAD" "r${round}-guest"
  fi
done

read -r closed hc gc n last <<< "$(room_state "$THREAD")"
snap "$THREAD" "99-final"
log "=== DONE: closed=$closed host_closed=$hc guest_closed=$gc msgs=$n ==="
log "transcript + all turn logs + snapshots in: $RUN"

# Content gate: closed=1 is necessary, not sufficient. score.sh decides
# PASS/FAIL/STALL on brief validity + leak + withheld-fact + escalation.
log "=== scoring (content assertions, not just close-state) ==="
bash "$HERE/score.sh" "$RUN" "$SCEN" 2>&1 | tee -a "$RUN/index.log"
