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
RUN="$HERE/runs/${SCEN}-${TS}-$$"       # logs/snapshots live here (gitignored); -$$ = unique per concurrent run
mkdir -p "$RUN/host" "$RUN/guest" "$RUN/snapshots"
# Agent WORK dirs live OUTSIDE the repo. If they were under the repo (as they
# were), a cwd-walking codex/claude would ingest the repo's AGENTS.md,
# CLAUDE.md, skill source and LESSONS_LEARNED — the test agent reading the
# product's own internals is exactly the maintainer-truth contamination the
# repo warns about (BO/BP/BQ) and the cross-platform audit flagged. Putting
# work dirs in a fresh /tmp tree makes the agent a real outsider.
WORKROOT="$(mktemp -d -t clawroom-e2e-work)"
echo "RUN: $RUN"
echo "WORKROOT (outside repo): $WORKROOT"
# Per-run isolation so several run-e2e.sh can run CONCURRENTLY without
# clobbering each other's room state / npm cache (the only remaining shared
# globals — workdir + CODEX_HOME are already per-WORKROOT). Both live under
# WORKROOT so the cleanup trap removes them too. Exported, so the agents'
# CLI calls (CLAWROOM_STATE_DIR) and npx (npm_config_cache) inherit them.
export CLAWROOM_STATE_DIR="$WORKROOT/cstate"
export npm_config_cache="$WORKROOT/npm-cache"
mkdir -p "$CLAWROOM_STATE_DIR" "$npm_config_cache"
# Cleanup on ANY exit (early abort, error, normal) — copied auth tokens live
# in WORKROOT and must never be left in /tmp. trap guarantees it even on the
# early `exit 1` paths below.
cleanup() { rm -rf "$WORKROOT" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN/index.log"; }

# ---- snapshot the room via operator export (recording, requirement 4) ----
snap() { # snap <thread> <label>
  [ -z "${1:-}" ] && return 0
  curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads/$1/export" \
    > "$RUN/snapshots/$2.json" 2>/dev/null || true
}
room_state() { # room_state <thread> -> "closed host_closed guest_closed nmsgs lastrole"
  curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads/$1/export" 2>/dev/null | python3 -c "
import sys,json
try:
  d=json.load(sys.stdin); t=d['thread']; m=d['messages']
  last=m[-1]['role'] if m else 'none'
  print(t.get('closed',0),t.get('host_closed',0),t.get('guest_closed',0),len(m),last)
except: print('0 0 0 0 none')
"
}
pending_ask_qid() { # <thread> <role> -> open pending_owner_ask question_id (or "")
  python3 - "$CLAWROOM_STATE_DIR/$1-$2.state.json" <<'PY'
import json,sys
try:
  s=json.load(open(sys.argv[1])); p=s.get('pending_owner_ask')
  print(p.get('question_id','') if isinstance(p,dict) else '')
except Exception: print('')
PY
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
  local side="$1" ch="$WORKROOT/$side/codex-home"
  if [ ! -f "$ch/auth.json" ]; then
    mkdir -p "$ch"
    [ -f "$HOME/.codex/auth.json" ] && cp "$HOME/.codex/auth.json" "$ch/auth.json"
  fi
  echo "$ch"
}

# ---- hard per-turn timeout (macOS has no `timeout`; use gtimeout if present,
# else a portable bg-watchdog). A stuck agent must not hang the run forever
# with a copied auth token sitting in WORKROOT. ----
TURN_TIMEOUT="${TURN_TIMEOUT:-600}"   # seconds per agent turn

# Model + reasoning-effort knobs. Defaults reproduce the strong-model runs;
# override to prove the weak-model floor (criterion 4), e.g.:
#   CODEX_EFFORT=low CLAUDE_MODEL=sonnet CLAUDE_EFFORT=low GUEST_DRIVER=claude \
#     bash evals/e2e/run-e2e.sh 01-sync
CODEX_MODEL="${CODEX_MODEL:-gpt-5.5}"
CODEX_EFFORT="${CODEX_EFFORT:-high}"
CLAUDE_MODEL="${CLAUDE_MODEL:-opus}"
CLAUDE_EFFORT="${CLAUDE_EFFORT:-xhigh}"
run_bg_timeout() { # run_bg_timeout <outfile> <cmd...>
  local of="$1"; shift
  "$@" > "$of" 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5; waited=$((waited+5))
    if [ "$waited" -ge "$TURN_TIMEOUT" ]; then
      echo "[HARNESS: turn killed after ${TURN_TIMEOUT}s timeout]" >> "$of"
      kill -TERM "$pid" 2>/dev/null; sleep 3; kill -KILL "$pid" 2>/dev/null
      return 124
    fi
  done
  wait "$pid" 2>/dev/null; return $?
}

# ---- one cold/resumed agent turn, fully logged + timed ----
# fire <side> <driver> <turn-no> <prompt-file-or-string> [resume]
fire() {
  local side="$1" driver="$2" n="$3" promptsrc="$4" mode="${5:-cold}"
  local wd="$WORKROOT/$side/work"; mkdir -p "$wd"   # OUTSIDE repo (no AGENTS.md/CLAUDE.md ingest)
  local CH; CH="$(setup_iso "$side")"
  local out="$RUN/$side/turn${n}.log"
  local prompt; prompt="$(cat "$promptsrc" 2>/dev/null || printf '%s' "$promptsrc")"
  local minfo; if [ "$driver" = "codex" ]; then minfo="$CODEX_MODEL/$CODEX_EFFORT"; else minfo="$CLAUDE_MODEL/$CLAUDE_EFFORT"; fi
  log "FIRE $side/$driver[$minfo] turn$n ($mode, timeout ${TURN_TIMEOUT}s)"
  local rc=0
  if [ "$driver" = "codex" ]; then
    if [ "$mode" = "resume" ]; then
      run_bg_timeout "$out" env CODEX_HOME="$CH" sh -c 'cd "$1" && shift && codex exec "$@"' _ "$wd" \
          -m "$CODEX_MODEL" -c model_reasoning_effort="$CODEX_EFFORT" --ignore-user-config \
          -s workspace-write -c sandbox_workspace_write.network_access=true \
          -c approval_policy=never --skip-git-repo-check \
          --add-dir "$npm_config_cache" --add-dir "$CLAWROOM_STATE_DIR" resume --last "$prompt"; rc=$?
    else
      run_bg_timeout "$out" env CODEX_HOME="$CH" sh -c 'cd "$1" && shift && codex exec "$@"' _ "$wd" \
          -m "$CODEX_MODEL" -c model_reasoning_effort="$CODEX_EFFORT" --ignore-user-config \
          -s workspace-write -c sandbox_workspace_write.network_access=true \
          -c approval_policy=never --skip-git-repo-check \
          --add-dir "$npm_config_cache" --add-dir "$CLAWROOM_STATE_DIR" "$prompt"; rc=$?
    fi
  elif [ "$driver" = "claude" ]; then
    if [ "$mode" = "resume" ]; then
      run_bg_timeout "$out" sh -c 'cd "$1" && shift && claude "$@"' _ "$wd" \
          -p "$prompt" --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" --permission-mode acceptEdits \
          --allowedTools "Bash,Read,Write,WebFetch,Glob,Grep" --continue; rc=$?
    else
      run_bg_timeout "$out" sh -c 'cd "$1" && shift && claude "$@"' _ "$wd" \
          -p "$prompt" --model "$CLAUDE_MODEL" --effort "$CLAUDE_EFFORT" --permission-mode acceptEdits \
          --allowedTools "Bash,Read,Write,WebFetch,Glob,Grep"; rc=$?
    fi
  fi
  [ "$rc" = "124" ] && log "  TIMEOUT ($TURN_TIMEOUT s) — $(wc -l < "$out") lines -> $out" \
                    || log "  exit $rc — $(wc -l < "$out") lines -> $out"
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
  curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads" 2>/dev/null | python3 -c "
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
RUN_DEADLINE=$(( $(date +%s) + ${GLOBAL_RUN_CAP:-1500} ))   # 25min hard wall-clock cap per run (belt to the per-turn timeout)
for round in $(seq 1 "$MAX_TURNS_PER_SIDE"); do
  read -r closed hc gc n last <<< "$(room_state "$THREAD")"
  log "state: closed=$closed host_closed=$hc guest_closed=$gc msgs=$n last=$last"
  [ "$closed" = "1" ] && { log "=== MUTUAL CLOSE ==="; break; }
  [ "$(date +%s)" -ge "$RUN_DEADLINE" ] && { log "=== GLOBAL CAP (${GLOBAL_RUN_CAP:-1500}s) hit — aborting; run did not converge ==="; break; }
  # Auto owner-reply (req 4: no manual intervention). The escalation
  # scenarios put the private mandate on the HOST owner, so the host
  # agent is the one that escalates (writes pending_owner_ask to state).
  # If a pending ask is open and the scenario ships a scripted owner
  # decision, answer it as the owner would, then resume the host so it
  # relays the decision into the room and proceeds — scripting the single
  # human-in-the-loop moment so escalation runs auto-complete + record.
  QID="$(pending_ask_qid "$THREAD" host)"
  if [ -n "$QID" ] && [ -f "$HERE/scenarios/${SCEN}.owner.txt" ]; then
    OWNER_ANS="$(cat "$HERE/scenarios/${SCEN}.owner.txt")"
    # The owner's decision (approve|reject) is declared explicitly in the
    # scenario assert — robust vs sniffing free-text owner voice (an approval
    # like "别再往上加" must not read as a rejection).
    DEC="$(python3 -c "import json;print(json.load(open('$HERE/scenarios/${SCEN}.assert.json')).get('owner_decision','approve'))" 2>/dev/null || echo approve)"
    env CLAWROOM_RELAY="$RELAY" "$REPO/skill/cli/clawroom" owner-reply \
      --room "$THREAD" --role host --question-id "$QID" \
      --decision "$DEC" --evidence "Owner replied in chat: $OWNER_ANS" \
      > "$RUN/host/owner-reply-r${round}.log" 2>&1 || true
    log "AUTO-OWNER-REPLY r${round}: qid=$QID decision=$DEC (scripted from ${SCEN}.owner.txt)"
    snap "$THREAD" "r${round}-owner-reply"
    ht=$((ht+1))
    fire host "$HOST_DRIVER" "$ht" "你问我的那个事我决定了：$OWNER_ANS — 你按这个继续，该跟对方说就说，该收尾就收尾。" resume
    snap "$THREAD" "r${round}-host"
    continue
  fi
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

# Save per-role state INTO the run dir so the run is self-scoreable forever
# (the next run's scrub wipes ~/.clawroom-v4; a recorded run must not depend
# on live state that gets destroyed — that made an earlier "PASS"
# unreproducible). Tokens redacted; the validators only need
# pending_owner_ask / owner_approvals / structure, not the token.
# The CLI writes room state to ~/.clawroom-v4 by default; fire() isolates
# CODEX_HOME but never HOME, so that's where the live state landed.
STATE_DIR="${CLAWROOM_STATE_DIR:-$HOME/.clawroom-v4}"
mkdir -p "$RUN/state"
for f in "$STATE_DIR/$THREAD"-*.state.json; do
  [ -e "$f" ] || continue
  python3 - "$f" "$RUN/state/$(basename "$f")" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for k in ("host_token","guest_token"):
    if k in d: d[k]="[redacted]"
json.dump(d, open(sys.argv[2],"w"), ensure_ascii=False, indent=2)
PY
done
log "saved redacted state snapshots to $RUN/state/"

# Content gate: closed=1 is necessary, not sufficient. score.sh decides
# PASS/FAIL/STALL on brief validity + CloseDraft validation + leak +
# withheld-fact + escalation. Score against the SAVED state (self-contained).
log "=== scoring (content assertions, not just close-state) ==="
CLAWROOM_STATE_DIR="$RUN/state" bash "$HERE/score.sh" "$RUN" "$SCEN" 2>&1 | tee -a "$RUN/index.log"
SCORE_RC="${PIPESTATUS[0]}"

cleanup; trap - EXIT INT TERM
log "cleaned WORKROOT"
# Propagate score.sh's verdict as the script's exit code (0=PASS, 1=FAIL,
# 2=STALL) so automation/CI can't read a failing score as success.
exit "$SCORE_RC"
