#!/usr/bin/env bash
# evals/e2e/unattended-coldstart.sh — the CORRECTED unattended acceptance harness.
#
# WHAT IT PROVES
#   The owner sends ONE cold message per side and then walks away. The AGENT
#   ITSELF — following SKILL.md step 3a/3b, where `clawroom arm` is now a DEFAULT
#   step, not an optional one — arms a macOS launchd wakeup for its own role. The
#   two armed jobs then self-drive the room to mutual close. The owner never
#   relays a turn, never nudges, never closes.
#
# THE FLAW THIS FIXES (why the OLD unattended harness false-passed)
#   The old harness SUPPLIED the scheduler and DROVE the turns itself (it called
#   the agent again every round, ran owner-reply, even bootstrapped launchd). So
#   it proved "a loop the HARNESS drives reaches close" — NOT "the room self-
#   drives once the agent arms it." A harness that drives turns can pass even if
#   the agent never arms anything. The fix is a hard architectural rule:
#
#       After the owner's 2 cold messages, THE HARNESS RETIRES.
#       From that point it fires NO claude/codex turn, runs NO
#       clawroom arm/post/poll/owner-reply, NO launchctl bootstrap.
#       It ONLY reads the relay admin export (read-only recording) and
#       tails the wake logs, then SCORES.
#
#   If the room reaches mutual close after the harness retired, the ONLY thing
#   that could have driven it is the agents' own armed launchd jobs. That is the
#   property under test, and it is unfakeable by harness machinery.
#
# CONTAMINATION LEDGER (the teeth)
#   Every harness-issued command (claude/codex/clawroom/launchctl) is run through
#   `ledger`, which appends `<ts> <phase> -- <cmd...>` to $RUN/harness-ledger.log.
#   phase ∈ {cold, retired}. The scorer asserts:
#     - the ledger shows ZERO `clawroom arm` and ZERO `launchctl bootstrap`
#       issued by the HARNESS (so the launchd jobs that exist were armed by the
#       AGENTS), and
#     - the ledger shows ZERO claude/codex turns and ZERO clawroom
#       post/poll/owner-reply in phase=retired (so the room self-drove).
#   ANY arm/fire/post/owner-reply/bootstrap in phase=retired ⇒ the run is
#   INVALID (contaminated): a DISTINCT exit code 9, not pass, not fail.
#
# MODES
#   unattended-coldstart.sh [scenario]      DEFAULT live mode (~25 min; lead runs)
#   unattended-coldstart.sh --selftest-mustfail        deterministic, no agent
#   unattended-coldstart.sh --selftest-contamination   deterministic, no agent
#
# macOS only (launchd). Reuses evals/e2e/score.sh's CloseDraft hard-wall
# validator and leak-scan patterns; reuses run-e2e.sh's admin-export snapshot
# pattern; emits a redacted bundle via scripts/redact-dogfood-run.sh.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
RELAY="${CLAWROOM_RELAY:-https://api.clawroom.cc}"
ADMIN_KEY="$(grep -h CLAWROOM_ADMIN_KEY "$REPO/docs/operator-admin-key.local.txt" 2>/dev/null | cut -d= -f2)"

# Scoring / exit-code contract (single source of truth, echoed in --help):
#   0  PASS         — every check passed; the AGENTS armed and the room
#                     self-drove to mutual close after the harness retired.
#   1  FAIL         — a check failed (agent-armed / self-drive / mutual-close /
#                     owner-actions / self-stop / no-leaks). A real, honest miss.
#   8  INCONCLUSIVE — the run could not produce a verdict for a NON-product
#                     reason (stale-skill precondition, missing admin key, host
#                     turn crashed before creating a room). Not a product result.
#   9  INVALID      — CONTAMINATED: the ledger shows the harness issued an
#                     arm/fire/post/owner-reply/bootstrap in phase=retired. The
#                     harness cheated (or a selftest deliberately cheated to
#                     prove the guard). Neither pass nor fail.
EXIT_PASS=0; EXIT_FAIL=1; EXIT_INCONCLUSIVE=8; EXIT_CONTAMINATED=9

MODE="${1:-default}"
SCEN_DEFAULT="01-sync"

# macOS guard — arm/launchd is darwin-specific; the whole harness is too.
if [ "$(uname -s)" != "Darwin" ]; then
  echo "unattended-coldstart: macOS only (arm uses launchd). Got $(uname -s)." >&2
  exit "$EXIT_INCONCLUSIVE"
fi

# ── persisted run dir (NON-Desktop, never deleted) ─────────────────────────
# Everything lives here: logs, ledger, snapshots, the stale-skill copy, the
# agents' work dirs. Under $HOME/.clawroom-dogfood (NOT Desktop/Documents/
# Downloads) so a launchd job the AGENT arms can cwd into the skill copy
# without tripping TCC (the gotcha the recipe warns about).
TS="$(date +%Y%m%d-%H%M%S)"
RUN="$HOME/.clawroom-dogfood/coldstart-${TS}-$$"
mkdir -p "$RUN"
LEDGER="$RUN/harness-ledger.log"
: > "$LEDGER"
SKILL_COPY="${COLDSTART_SKILL_DIR:-$RUN/skill}"   # stale-checked skill copy; override to a ~/Desktop path to exercise the arm relocate
# Per-run state dir lives under the run dir too, so it is co-located with the
# skill copy and survives (never scrubbed) for forensic scoring. The AGENT's
# arm/heartbeat inherit this via CLAWROOM_STATE_DIR.
export CLAWROOM_STATE_DIR="$RUN/cstate"
mkdir -p "$CLAWROOM_STATE_DIR"

# log -> STDERR (+ index.log file), never stdout. score_run/selftests capture
# stdout via $() to assert on the scorecard/RESULT lines; routing progress logs
# to stderr keeps that captured stdout clean (only the scorecard + RESULT).
log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$RUN/index.log" >&2; }

# ── the contamination ledger wrapper ──────────────────────────────────────
# PHASE is the current lifecycle tag; ledger stamps every harness-issued
# command with it. Start in cold; flip to retired at RETIRE.
PHASE="cold"
# ledger <cmd...> — append the command to the ledger with ts+phase, then RUN it.
# Use this for ALL harness-issued claude/codex/clawroom/launchctl commands. The
# read-only recording ops (curl admin export, tail) also go through it so the
# ledger is a complete account of what the harness did in each phase.
ledger() {
  printf '%s\t%s\t--\t%s\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$PHASE" "$*" >> "$LEDGER"
  "$@"
}

# Read-only relay admin export (recording, requirement 6). Tagged retired when
# called after RETIRE. This NEVER mutates the room — it is a GET against /admin.
snap() { # snap <thread> <label>
  [ -z "${1:-}" ] && return 0
  ledger curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" \
    "$RELAY/admin/threads/$1/export" > "$RUN/snapshots/$2.json" 2>/dev/null || true
}
room_state() { # room_state <thread> -> "closed host_closed guest_closed nmsgs lastrole"
  curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" \
    "$RELAY/admin/threads/$1/export" 2>/dev/null | node -e '
let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{
  try{const d=JSON.parse(s),t=d.thread||{},m=d.messages||[];
    const last=m.length?m[m.length-1].role:"none";
    process.stdout.write([t.closed||0,t.host_closed||0,t.guest_closed||0,m.length,last].join(" "));
  }catch(e){process.stdout.write("0 0 0 0 none");}
});'
}
# Only accept a room created AFTER this run began (a stale room from a prior run
# must never be mistaken for the one the host just made).
newest_thread() { # newest_thread <since_epoch_ms>
  curl -s --max-time 30 -H "x-clawroom-admin-key: $ADMIN_KEY" "$RELAY/admin/threads" 2>/dev/null | node -e '
let s="";const since=parseInt(process.argv[1]||"0",10);
process.stdin.on("data",d=>s+=d).on("end",()=>{
  try{for(const t of (JSON.parse(s).threads||[])){ if(parseInt(t.created_at||0,10)>=since){process.stdout.write(t.thread_id);return;} } process.stdout.write("");}
  catch(e){process.stdout.write("");}
});' "$1"
}

# ── launchd label helpers (match skill/cli/clawroom armLabel exactly) ──────
label_for() { echo "cc.clawroom.wake.$1-$2"; }   # <room> <role>
launchd_loaded() { launchctl list "$1" >/dev/null 2>&1; }   # <label>
# Teardown: disarm any wake job for THIS room (host + guest) and rm per-room
# bases. Runs in an EXIT trap so a crash or cap never leaves launchd cruft.
# NOTE: teardown is NOT ledgered as a harness turn — it runs after scoring, is
# pure cleanup, and (critically) booting a job out is not "driving the room."
DISARM_ROOM=""
teardown() {
  local r="$DISARM_ROOM"
  [ -z "$r" ] && return 0
  for role in host guest; do
    local lbl base plist
    lbl="$(label_for "$r" "$role")"
    launchctl bootout "gui/$(id -u)/$lbl" 2>/dev/null || true
    plist="$HOME/Library/LaunchAgents/$lbl.plist"
    rm -f "$plist" 2>/dev/null || true
    base="$HOME/.clawroom/$r-$role"
    rm -rf "$base" 2>/dev/null || true
  done
}
trap teardown EXIT INT TERM

# ===========================================================================
#  STALE-SKILL PRECONDITION (Rule 11) — FIRST, MANDATORY
# ===========================================================================
# The run MUST use a non-Desktop copy of skill/. Assert:
#   (a) the copy is NOT under ~/Desktop|Documents|Downloads (TCC), and
#   (b) a content hash of copy cli/ + lib/ + the SKILL.md `version` EQUALS the
#       repo skill/'s (current build).
# Abort LOUD "stale skill — run INVALID" (exit INCONCLUSIVE) on mismatch. Log
# both hashes. This is what stops a run against a stale install from being read
# as a real result.
skill_version() { # parse metadata.version from a SKILL.md frontmatter
  node -e '
const fs=require("fs");const t=fs.readFileSync(process.argv[1],"utf8");
const m=t.match(/^\s*version:\s*"?([^"\n]+)"?\s*$/m);
process.stdout.write(m?m[1].trim():"NO_VERSION");' "$1"
}
# Content hash over cli/ + lib/ ONLY (the executable surface), plus the parsed
# SKILL.md version appended as a line. References/docs churn independently and
# would make the hash brittle without strengthening the "same build" guarantee;
# the version string + the code dirs are what determine runtime behavior.
skill_content_hash() { # <skill-dir>
  local dir="$1"
  { (cd "$dir" && find cli lib -type f \( -name '*.mjs' -o -name '*.sh' -o -name 'clawroom' \) \
        -exec shasum -a 256 {} \; | sort -k2)
    echo "SKILL.md.version=$(skill_version "$dir/SKILL.md")"
  } | shasum -a 256 | awk '{print $1}'
}

precondition_stale_skill() {
  log "=== Rule-11 stale-skill precondition ==="
  # (b-source) copy the repo skill into the persisted, non-Desktop run dir.
  rm -rf "$SKILL_COPY"
  mkdir -p "$SKILL_COPY"
  # cp -R the skill payload (cli/ lib/ references/ SKILL.md). We copy from the
  # repo so the run uses a frozen snapshot, not a live-edited tree.
  cp -R "$REPO/skill/." "$SKILL_COPY/"

  # (a) TCC assertion: the copy must NOT be under a TCC-protected dir — UNLESS
  # we're deliberately staging there via COLDSTART_SKILL_DIR to exercise arm's
  # RELOCATE (arm copies the skill out to ~/.clawroom/skill-runtime and runs the
  # wake from there, so a Desktop install no longer breaks the wake). The runtime
  # rule-11 check later confirms the agents armed from the relocated non-TCC dir.
  local resolved; resolved="$(cd "$SKILL_COPY" && pwd -P)"
  if [ -z "${COLDSTART_SKILL_DIR:-}" ]; then
    for tcc in "$HOME/Desktop" "$HOME/Documents" "$HOME/Downloads"; do
      case "$resolved/" in
        "$tcc/"*)
          log "ABORT: skill copy is under TCC dir $tcc ($resolved) — a launchd job armed by the agent could not cwd there."
          echo "RESULT: INCONCLUSIVE — stale skill: copy under TCC-protected dir"
          exit "$EXIT_INCONCLUSIVE" ;;
      esac
    done
  else
    log "relocate-test mode: skill copy under $resolved (TCC) — arm is expected to relocate out to ~/.clawroom/skill-runtime."
  fi

  # (b) content-hash equality: copy must match the repo (current build).
  local repo_hash copy_hash
  repo_hash="$(skill_content_hash "$REPO/skill")"
  copy_hash="$(skill_content_hash "$SKILL_COPY")"
  log "repo  skill hash: $repo_hash (version $(skill_version "$REPO/skill/SKILL.md"))"
  log "copy  skill hash: $copy_hash (version $(skill_version "$SKILL_COPY/SKILL.md"))"
  if [ "$repo_hash" != "$copy_hash" ]; then
    log "ABORT: skill copy content hash != repo build hash — STALE SKILL."
    echo "RESULT: INCONCLUSIVE — stale skill: copy hash $copy_hash != repo hash $repo_hash; run INVALID"
    exit "$EXIT_INCONCLUSIVE"
  fi
  log "stale-skill precondition OK — copy is non-Desktop and matches current build."
}

# ===========================================================================
#  COLD OWNER TURNS  (phase=cold)
# ===========================================================================
# Hard per-turn timeout (macOS lacks `timeout`; use gtimeout if present else a
# portable bg watchdog). A stuck cold turn must not hang the run.
TURN_TIMEOUT="${TURN_TIMEOUT:-600}"
run_bg_timeout() { # run_bg_timeout <outfile> <cmd...>
  local of="$1"; shift
  "$@" > "$of" 2>&1 &
  local pid=$! waited=0
  while kill -0 "$pid" 2>/dev/null; do
    sleep 5; waited=$((waited+5))
    if [ "$waited" -ge "$TURN_TIMEOUT" ]; then
      echo "[HARNESS: cold turn killed after ${TURN_TIMEOUT}s timeout]" >> "$of"
      kill -TERM "$pid" 2>/dev/null; sleep 3; kill -KILL "$pid" 2>/dev/null
      return 124
    fi
  done
  wait "$pid" 2>/dev/null; return $?
}

# One cold `claude -p` owner turn. cwd is the agent's OWN work dir (NOT the repo,
# so the agent never ingests the product's CLAUDE.md/AGENTS.md/LESSONS — the
# maintainer-truth contamination the repo warns about). The prompt is a NATURAL
# owner request; the only technical content is the skill path the owner pastes.
# Routed through `ledger` so the ledger records exactly 2 cold claude turns.
cold_turn() { # cold_turn <side> <workdir> <prompt> <outfile>
  local side="$1" wd="$2" prompt="$3" out="$4"
  mkdir -p "$wd"
  log "COLD TURN $side (sonnet, timeout ${TURN_TIMEOUT}s)"
  ledger run_bg_timeout "$out" \
    sh -c 'cd "$1" && shift && claude "$@"' _ "$wd" \
      -p "$prompt" --model sonnet --permission-mode acceptEdits \
      --allowedTools "Bash,Read,Write,Glob,Grep"
  local rc=$?
  [ "$rc" = "124" ] && log "  COLD TURN $side TIMEOUT" || log "  COLD TURN $side exit $rc — $(wc -l < "$out" | tr -d ' ') lines"
}

run_default() {
  local scen="${1:-$SCEN_DEFAULT}"
  log "=== unattended-coldstart DEFAULT mode — scenario $scen ==="
  log "RUN dir (persisted, non-Desktop): $RUN"
  [ -z "$ADMIN_KEY" ] && { log "ABORT: no CLAWROOM_ADMIN_KEY (docs/operator-admin-key.local.txt) — cannot read the read-only admin export to score."; echo "RESULT: INCONCLUSIVE — admin key missing"; exit "$EXIT_INCONCLUSIVE"; }

  precondition_stale_skill

  local host_work="$RUN/host-work" guest_work="$RUN/guest-work"
  mkdir -p "$host_work" "$guest_work" "$RUN/snapshots"

  # ── Owner action #1 (phase=cold): cold HOST turn ────────────────────────
  # NATURAL owner sync prompt. States the skill path + intent (routine sync,
  # authorized to close). Per SKILL.md the agent will: open the room (atomic
  # create+opening), run `clawroom arm` ITSELF, post the opening, and — we ask
  # it explicitly, the one mechanical instruction an owner would naturally give
  # — write the invite URL to $RUN/invite.txt so the "human" can forward it.
  # No test/harness/Phase/OWNER_ASK vocabulary (Rule 7).
  PHASE="cold"
  local invite_file="$RUN/invite.txt"
  # Scenario kind: "sync" (default) → condition-1 no-ask self-drive (the agent
  # defers hard calls to the upcoming meeting, F11). "boundary" → a DEAL-CLOSING
  # task with NO meeting to defer to and a budget ceiling the peer's quote
  # crosses, so the agent genuinely cannot punt and MUST escalate → condition-2
  # (auto-owner-reply answers with a decision; the agent self-drives to close).
  local SCEN_KIND="${COLDSTART_SCENARIO:-sync}"
  local OWNER_REPLY_EVIDENCE GUEST_OPENING GUEST_INTENT HOST_PROMPT_OVERRIDE=""
  if [ "$SCEN_KIND" = "boundary" ]; then
    HOST_PROMPT_OVERRIDE="我有个 ClawRoom 的 skill 装在这个目录：$SKILL_COPY 。先进去读它的 SKILL.md，照着做。

我找了个做小程序外包的，姓陈，他的助手会上来跟你谈。你替我把这个外包的活儿谈定——范围、价钱、交付时间，今天就得有结论，对方等着定，没有\"回头电话里再说\"这一说。我的底线：预算上限 5000 块，含两轮修改、两周内交付。要是他报价超 5000、或者要砍修改轮次/拖长交付，这种涉及钱和硬约束的，你别自己答应、也别自己回绝、更别往后拖——先回来问我，我自己拍板。其它细节你看着办，谈完给我个结论。

开好房间后，把你要我转发给陈工那边的那整段邀请内容（CLI 给你的那段，不只是链接）原样写到这个文件里：$invite_file"
    GUEST_OPENING='我合伙人让我把一个小程序外包的活儿谈定，对方在等我。你直接跟他们助手谈，能定就定，今天得有个明确结论。'
    GUEST_INTENT='我这边：我是陈工的助手，陈工接小程序外包。这个活儿我们报价 6200，含两轮修改，交付要三周（两周排不开）。我需要对方今天就给个明确答复好排期，别拖。'
    OWNER_REPLY_EVIDENCE='决定：6200 超了我 5000 的上限。先还价 5500、两轮修改、两周半交付；他要是低于 5800 不肯做、或坚持三周，那最多接受 5800、三周，但修改轮次不能砍。按这个谈定。'
  else
    GUEST_OPENING='我合伙人发我这个，说让咱俩的助手先聊聊，你看着办，弄完跟我讲讲对面啥情况就行。这是例行对齐，没有新承诺，你可以自己谈完、该收尾就收尾。'
    GUEST_INTENT='我这边：我负责这个旅行小程序拉用户、找渠道，最近在跟两家渠道谈，还行。我习惯有事留言、别老开会。这次我主要想知道东西啥时候能有个我能拿出去给渠道看的版本、到时候要我准备啥。'
    OWNER_REPLY_EVIDENCE="Routine sync — you're authorized to proceed. Use the 'early interactive demo' framing, make no new commitments, and defer any hard specifics to the call itself. Answer the peer with what you have and keep moving toward close."
  fi
  local host_prompt
  host_prompt="$(cat <<EOF
我有个 ClawRoom 的 skill 装在这个目录：$SKILL_COPY 。先进去读它的 SKILL.md，照着做。

我跟我合伙人小林一起搞那个旅行小程序，我管做产品，他管拉用户、找渠道。我们晚点要通个话。你先去跟他的助手碰一下，把我们各自现在做到哪了、平时怎么干活、这次想从对方那儿要到啥，先对一对，回头给我说个一二三，我打电话时就不用从头扯了。

这是例行的对齐，没有新的承诺或花钱，你可以自己把这事谈完、该收尾就收尾，不用每一步都回来问我。

我这边：核心的行程自动生成已经能用了，界面还在弄，大概两周能有个能给外人看的版本——那个版本是用户能自己上手操作的可交互 demo（能自己生成行程、走核心流程），界面还糙、高级功能还没有，对外就说成"早期可交互 demo"、别说成成品、也别承诺具体完成度。两周是软目标不是硬承诺，真有变化我会提前几天知道。我去跟渠道谈之前，我想知道对面那两家渠道的画像、他们对产品有没有特殊要求。这些你都可以直接代表我谈，不用回来问我。我一般晚上干活，白天回消息慢。

开好房间后，把你要我转发给小林的那整段邀请内容（CLI 给你的那段，包含让对方助手怎么加入的完整说明，不只是链接）原样写到这个文件里：$invite_file
EOF
)"
  # Boundary scenario replaces the host mandate with the deal-closing task.
  [ -n "$HOST_PROMPT_OVERRIDE" ] && host_prompt="$HOST_PROMPT_OVERRIDE"
  cold_turn host "$host_work" "$host_prompt" "$RUN/host-cold.log"

  # Discover the room the host just created (created after run start).
  local RUN_START_MS THREAD
  RUN_START_MS="$(node -e 'process.stdout.write(String(Date.now()-120000))')"   # small backdate slack
  THREAD="$(newest_thread "$RUN_START_MS")"
  log "detected room: ${THREAD:-NONE}"
  if [ -z "$THREAD" ]; then
    # Honest split: harness crash vs product (agent ran but made no room).
    if grep -qiE "unbound variable|command not found|No such file|syntax error" "$RUN/host-cold.log" 2>/dev/null; then
      log "host cold turn crashed before creating a room (harness/setup) — not a product result"
      echo "RESULT: INCONCLUSIVE — host cold turn crashed before creating a room"
      exit "$EXIT_INCONCLUSIVE"
    fi
    log "host agent ran but never created a room"
    echo "RESULT: FAIL — host agent ran from the owner's request but never created a room"
    exit "$EXIT_FAIL"
  fi
  DISARM_ROOM="$THREAD"          # arm teardown trap now that we know the room
  snap "$THREAD" "01-after-host-cold"

  # Recover the invite: prefer the file the agent wrote; fall back to grepping
  # the host turn log for the /i/ invite URL (worst-case human forward).
  # Faithful flow: a real partner forwards the WHOLE public_message block the
  # host's create produced (it says "paste this to your AI assistant: install,
  # cd, join --invite URL, read SKILL.md"), NOT a bare URL. So the guest prompt
  # below embeds that block; we also keep the bare URL for harness bookkeeping.
  local INVITE="" FORWARD_BLOCK=""
  if [ -s "$invite_file" ]; then
    FORWARD_BLOCK="$(head -c 6000 "$invite_file" | tr -d '\r')"
    INVITE="$(grep -oE "$RELAY/i/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+" "$invite_file" | head -1)"
  fi
  [ -z "$INVITE" ] && INVITE="$(grep -oE "$RELAY/i/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+" "$RUN/host-cold.log" | head -1)"
  if [ -z "$INVITE" ]; then
    log "host agent created a room but surfaced no invite to forward"
    echo "RESULT: FAIL — host agent created a room but never produced an invite URL to forward"
    exit "$EXIT_FAIL"
  fi
  # If the agent wrote only a bare URL (no join instructions), reconstruct a
  # minimal real forward block so the guest still gets "join via CLI" guidance.
  if ! printf '%s' "$FORWARD_BLOCK" | grep -q "join --invite"; then
    FORWARD_BLOCK="Paste this whole thing to your AI assistant — it handles the rest:
1. Use the clawroom skill already installed (cd into its directory; it has SKILL.md).
2. Join by RUNNING this command. The URL is an ARGUMENT for the CLI, not a web page — do not open, fetch, or browse it:
   ./cli/clawroom join --invite \"$INVITE\"
3. Then read SKILL.md and follow it from \"First contact\"."
  fi
  log "invite captured (url=yes, forward-block $(printf '%s' "$FORWARD_BLOCK" | wc -c | tr -d ' ') chars)"

  # ── Owner action #2 (phase=cold): cold GUEST turn ───────────────────────
  # Prompt = the forwarded invite + the guest owner's intent. The agent joins,
  # arms ITSELF (guest role), and replies. Still natural owner voice.
  PHASE="cold"
  local guest_prompt
  guest_prompt="$(cat <<EOF
$GUEST_OPENING

（注意：clawroom 这个 skill 本机已经装好了，就在这个目录：$SKILL_COPY —— 直接 cd 进去用它，下面那段里让你用 npx 安装 skill 的步骤可以跳过，其余照做，尤其是里面的 join 命令。）

$FORWARD_BLOCK

$GUEST_INTENT
EOF
)"
  cold_turn guest "$guest_work" "$guest_prompt" "$RUN/guest-cold.log"
  snap "$THREAD" "02-after-guest-cold"

  # ── Rule-11 (RUNTIME): both agents must have ARMED from the CURRENT build ──
  # The precondition hash only covers the copy WE pre-staged for the host. An
  # agent can npx-install a DIFFERENT (stale GitHub) skill at runtime and arm
  # from it (finding F7) — and a green from a stale skill is a false signal. So
  # every loaded wake job's CLAWROOM_SKILL_DIR must hash-match the repo build,
  # else the run is INVALID. This is the runtime half of rule 11.
  local rt_repo_hash rt_role rt_lbl rt_dir rt_hash rt_host_ok=no rt_guest_ok=no
  rt_repo_hash="$(skill_content_hash "$REPO/skill")"
  for rt_role in host guest; do
    rt_lbl="$(label_for "$THREAD" "$rt_role")"
    if ! launchd_loaded "$rt_lbl"; then log "armed-skill[$rt_role]: NOT LOADED (agent did not arm)"; continue; fi
    rt_dir="$(launchctl print "gui/$(id -u)/$rt_lbl" 2>/dev/null | grep -oE 'CLAWROOM_SKILL_DIR => [^ ]+' | awk '{print $3}')"
    rt_hash="$( [ -n "$rt_dir" ] && [ -d "$rt_dir" ] && skill_content_hash "$rt_dir" || echo MISSING )"
    log "armed-skill[$rt_role]: ${rt_dir:-NONE} hash=$rt_hash"
    if [ "$rt_hash" != "$rt_repo_hash" ]; then
      log "ABORT(INVALID): $rt_role armed from a STALE/non-current skill (${rt_dir:-none}) — hash $rt_hash != repo $rt_repo_hash. F7: a runtime npx-install pulled a stale GitHub build."
      echo "RESULT: INCONCLUSIVE — $rt_role armed from a stale skill (hash mismatch vs current build); run INVALID per rule 11"
      exit "$EXIT_INCONCLUSIVE"
    fi
    [ "$rt_role" = host ] && rt_host_ok=yes || rt_guest_ok=yes
  done
  if [ "$rt_host_ok" = yes ] && [ "$rt_guest_ok" = yes ]; then
    # Marker for the agent-armed scorer: BOTH agents armed from the CURRENT build
    # at retire. The scorer reads THIS (not post-close job presence — jobs self-
    # cancel on close, which would otherwise false-FAIL a passing run, F9).
    : > "$RUN/.armed-current"
    log "rule-11 RUNTIME check OK — both armed agents use the current build."
  else
    log "rule-11 RUNTIME: not both agents armed at retire (host=$rt_host_ok guest=$rt_guest_ok) — agent-armed will FAIL"
  fi

  # ========================================================================
  #  RETIRE  (phase=retired)
  # ========================================================================
  # From here the harness fires NO claude/codex turn, runs NO clawroom
  # arm/post/poll, NO launchctl bootstrap. It reads the relay admin export
  # (read-only), tails the wake logs, and — as the attentive OWNER — answers any
  # escalation via clawroom owner-reply. owner-reply is the OWNER's role for
  # condition 2: it records the owner's decision but does NOT post to the room or
  # compose the agent's turn, so it is not driving the agent. A no-ask run never
  # triggers it (pure condition-1 self-drive); an ask run -> condition 2. The
  # room self-drives on the agents' OWN armed launchd jobs.
  PHASE="retired"
  log "=== RETIRED — harness will only record + tail; the AGENTS' armed launchd drives the room now ==="
  printf '%s\tRETIRE\t--\t(harness retired; only read-only admin export + log tails follow)\n' \
    "$(date +%Y-%m-%dT%H:%M:%S)" >> "$LEDGER"

  # Watch until mutual close OR a hard 25-min wall-clock cap. We POLL the read-
  # only admin export on an interval (a GET, never a mutation) and copy the
  # agents' own wake logs into the run dir for the bundle. We do NOT touch the
  # room and do NOT spawn the agent — the launchd jobs do that themselves.
  local CAP_SECONDS="${COLDSTART_CAP:-2700}"   # 45 min — cold-pickup wake turns are slow (F8); a multi-turn close needs headroom
  local deadline=$(( $(date +%s) + CAP_SECONDS ))
  local closed=0 hc=0 gc=0 n=0 last="none" i=0
  while :; do
    read -r closed hc gc n last <<< "$(room_state "$THREAD")"
    log "retired-watch: closed=$closed host_closed=$hc guest_closed=$gc msgs=$n last=$last armed=[$(launchd_loaded "$(label_for "$THREAD" host)" && echo host)$(launchd_loaded "$(label_for "$THREAD" guest)" && echo ' guest')]"
    # The attentive OWNER answers any escalation (condition 2). owner-reply only
    # records the owner's decision; the AGENT still wakes (owner_answered_wake)
    # and composes+posts its own turn. Answer each question_id ONCE.
    for ar_role in host guest; do
      local ar_qid
      ar_qid="$(node -e "try{const s=require('$CLAWROOM_STATE_DIR/${THREAD}-${ar_role}.state.json');process.stdout.write(s.pending_owner_ask?String(s.pending_owner_ask.question_id):'')}catch(e){}" 2>/dev/null)"
      [ -z "$ar_qid" ] && continue
      grep -qs "^$ar_role:$ar_qid$" "$RUN/.owner-answered" 2>/dev/null && continue
      printf '%s:%s\n' "$ar_role" "$ar_qid" >> "$RUN/.owner-answered"
      log "OWNER auto-reply: $ar_role escalated '$ar_qid' — answering as the owner (condition 2)"
      printf '%s\towner\t--\towner-reply role=%s qid=%s (attentive owner answers escalation)\n' "$(date +%Y-%m-%dT%H:%M:%S)" "$ar_role" "$ar_qid" >> "$LEDGER"
      CLAWROOM_RELAY="$RELAY" CLAWROOM_STATE_DIR="$CLAWROOM_STATE_DIR" "$SKILL_COPY/cli/clawroom" owner-reply \
        --room "$THREAD" --role "$ar_role" --question-id "$ar_qid" --decision approve \
        --evidence "$OWNER_REPLY_EVIDENCE" \
        >> "$RUN/owner-reply-$ar_role.log" 2>&1 || log "owner-reply failed for $ar_role/$ar_qid (see owner-reply-$ar_role.log)"
    done
    [ "$closed" = "1" ] && { log "=== MUTUAL CLOSE (self-driven) ==="; break; }
    [ "$(date +%s)" -ge "$deadline" ] && { log "=== CAP (${CAP_SECONDS}s) hit — room did not self-drive to close in time ==="; break; }
    i=$((i+1)); sleep 30
  done

  # Copy the agents' own wake logs (written by their launchd ticks) into the run
  # dir under the names redact-dogfood-run.sh expects. These are reads, not
  # drives. Per-room wake logs live at $CLAWROOM_STATE_DIR/wakeup-<room>-<role>.log.
  for role in host guest; do
    local wl="$CLAWROOM_STATE_DIR/wakeup-$THREAD-$role.log"
    [ -f "$wl" ] && cp "$wl" "$RUN/wakeup-$role.log" 2>/dev/null || true
  done
  snap "$THREAD" "99-final"

  # Persist redacted per-role state INTO the run dir so the run is self-scoreable
  # forever (the validators need pending_owner_ask/owner_approvals/structure, not
  # the token). This mirrors run-e2e.sh's self-contained-state convention.
  mkdir -p "$RUN/state"
  for f in "$CLAWROOM_STATE_DIR/$THREAD"-*.state.json; do
    [ -e "$f" ] || continue
    node -e '
const fs=require("fs");const d=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));
for(const k of ["host_token","guest_token"]) if(k in d) d[k]="[redacted]";
fs.writeFileSync(process.argv[2],JSON.stringify(d,null,2));' "$f" "$RUN/state/$(basename "$f")"
  done

  # ── SCORE ───────────────────────────────────────────────────────────────
  score_run "$THREAD" "$scen"
  local rc=$?

  # ── emit a redacted, shareable bundle ────────────────────────────────────
  if [ -x "$REPO/scripts/redact-dogfood-run.sh" ]; then
    log "=== emitting redacted bundle ==="
    bash "$REPO/scripts/redact-dogfood-run.sh" "$RUN" 2>&1 | tee -a "$RUN/index.log" || true
  fi
  return "$rc"
}

# ===========================================================================
#  SCORER  — each check PASS/FAIL; contamination ⇒ INVALID(9)
# ===========================================================================
# Reused by the live default mode AND both selftests, so the falsifiability of
# the SAME scorer is what the triad meta-modes test.
#
# Args: <thread> <scenario>
# Globals it reads: $RUN (snapshots/state/ledger), $CLAWROOM_STATE_DIR, $RELAY.
# Returns: EXIT_PASS / EXIT_FAIL / EXIT_CONTAMINATED.
score_run() {
  local THREAD="$1" SCEN="${2:-$SCEN_DEFAULT}"
  local FINAL="$RUN/snapshots/99-final.json"
  local STATE_DIR; if [ -d "$RUN/state" ]; then STATE_DIR="$RUN/state"; else STATE_DIR="$CLAWROOM_STATE_DIR"; fi
  local ASSERT="$HERE/scenarios/${SCEN}.assert.json"
  log "=== SCORING (thread=$THREAD scenario=$SCEN) ==="

  # ── contamination GUARD (runs FIRST — its verdict overrides pass/fail) ────
  # The ledger must show ZERO arm/fire(claude|codex)/post/owner-reply/bootstrap
  # issued by the harness in phase=retired. If any appears, the harness drove
  # the room after retiring → the run is INVALID, distinct exit 9. We match the
  # phase column (\tretired\t) then the offending command token after the `--`.
  # Pattern note: the ledger separates its marker with a TAB (\t--\t), so a
  # pattern that tried to anchor on a literal "-- " (space) never matched and
  # silently passed every contamination check. awk has already isolated the
  # phase=retired rows by the tab-delimited $2; we match the command TOKENS
  # directly in the (tab-delimited) command field.
  local CONTAM
  CONTAM="$(awk -F'\t' '$2=="retired"' "$LEDGER" 2>/dev/null \
    | grep -E '(clawroom (arm|post|poll)|launchctl bootstrap|( |^)claude( |$)|( |^)codex( |$)|run_bg_timeout)' \
    || true)"
  if [ -n "$CONTAM" ]; then
    echo "CONTAMINATION (harness-issued drive command(s) in phase=retired):"
    echo "$CONTAM" | sed 's/^/  /'
    echo "RESULT: INVALID (contaminated) — the harness issued a drive command after retiring; this is neither pass nor fail"
    return "$EXIT_CONTAMINATED"
  fi

  local CHK_armed="?" CHK_selfdrive="?" CHK_close="?" CHK_owner="?" CHK_selfstop="?" CHK_leak="?"
  local fail_reasons=()

  # ── check: agent-armed ───────────────────────────────────────────────────
  # The launchd jobs cc.clawroom.wake.<room>-host AND -guest exist, AND the
  # ledger shows the harness issued ZERO `clawroom arm` and ZERO
  # `launchctl bootstrap` (in ANY phase) → therefore the AGENTS armed them.
  # FAIL if either job is missing, OR if the harness armed (would invalidate the
  # provenance even if the job exists).
  local harness_armed
  harness_armed="$(grep -E '(clawroom arm|launchctl bootstrap)' "$LEDGER" 2>/dev/null || true)"
  # Provenance: the AGENTS armed (not the harness). Assert from RETIRE-time
  # evidence (the .armed-current marker, written only when BOTH wake jobs were
  # loaded AND hash-matched the current build at retire) — NOT post-close job
  # presence, because on a successful close the jobs SELF-CANCEL, so "loaded now"
  # would false-FAIL a passing run (finding F9).
  if [ -n "$harness_armed" ]; then
    CHK_armed="FAIL"
    fail_reasons+=("agent-armed: the HARNESS issued arm/bootstrap (provenance broken) -> $(echo "$harness_armed" | head -1)")
  elif [ -f "$RUN/.armed-current" ]; then
    CHK_armed="PASS"
  else
    CHK_armed="FAIL"
    fail_reasons+=("agent-armed: the agents did not both arm from the current build at retire (no .armed-current marker; see armed-skill log lines)")
  fi

  # ── check: self-drive ────────────────────────────────────────────────────
  # The ledger shows ZERO claude/codex turns and ZERO clawroom post/poll/
  # owner-reply in phase=retired. (If the contamination guard above passed, this
  # is necessarily satisfied; we assert it explicitly as its own named check so
  # the report is legible and a future ledger-format change can't silently drop
  # it.)
  local retired_drives
  retired_drives="$(awk -F'\t' '$2=="retired"' "$LEDGER" 2>/dev/null \
    | grep -E '(clawroom (post|poll)|( |^)claude( |$)|( |^)codex( |$)|run_bg_timeout)' || true)"
  if [ -z "$retired_drives" ]; then CHK_selfdrive="PASS"; else
    CHK_selfdrive="FAIL"; fail_reasons+=("self-drive: harness issued a turn/post in phase=retired")
  fi

  # ── check: mutual close + both CloseDrafts validate (reuse score.sh wall) ─
  # closed=1 AND validateCloseDraft + validateCloseAgainstState pass for BOTH
  # closes, against each role's real (token-redacted) state file. Same JS the
  # shipped CLI runs; written to a temp file (avoids nested-quote fragility);
  # args passed as argv, not interpolated.
  local closed_flag
  closed_flag="$(node -e '
const fs=require("fs");try{const d=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));process.stdout.write(String((d.thread||{}).closed||0));}catch(e){process.stdout.write("0");}' "$FINAL" 2>/dev/null)"
  local VALJS; VALJS="$(mktemp -t clawroom-cs-val).mjs"
  cat > "$VALJS" <<'JS'
import fs from 'node:fs';
const [repo, final, stateDir, thread] = process.argv.slice(2);
const { validateCloseDraft, validateCloseAgainstState } = await import(repo + '/skill/lib/close.mjs');
let f; try { f = JSON.parse(fs.readFileSync(final, 'utf8')); } catch { console.log(JSON.stringify({ n: 0, problems: ['no final snapshot'] })); process.exit(0); }
const closes = (f.messages || []).filter(m => m.kind === 'close');
const problems = [];
for (const c of closes) {
  let draft;
  try { draft = JSON.parse(c.text); } catch { problems.push(c.role + ': close text is not JSON'); continue; }
  const d = validateCloseDraft(draft);
  if (!d.ok) problems.push(c.role + ': schema ' + JSON.stringify(d.issues.map(i => i.code)));
  const p = stateDir + '/' + thread + '-' + c.role + '.state.json';
  let st = null;
  try { st = JSON.parse(fs.readFileSync(p, 'utf8')); } catch {}
  if (st) { const s = validateCloseAgainstState(draft, st); if (!s.ok) problems.push(c.role + ': state ' + JSON.stringify(s.issues.map(i => i.code))); }
  else problems.push(c.role + ': no state file to validate against (' + p + ')');
}
console.log(JSON.stringify({ n: closes.length, problems }));
JS
  local VAL; VAL="$(node "$VALJS" "$REPO" "$FINAL" "$STATE_DIR" "$THREAD" 2>&1)"; rm -f "$VALJS"
  log "closedraft validation: $VAL"
  local val_problems val_n
  val_problems="$(printf '%s' "$VAL" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{const r=JSON.parse(s);process.stdout.write((r.problems||[]).join("; "));}catch(e){process.stdout.write("validator-error: "+s.slice(0,120));}});')"
  val_n="$(printf '%s' "$VAL" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(String(JSON.parse(s).n||0));}catch(e){process.stdout.write("0");}});')"
  if [ "$closed_flag" = "1" ] && [ -z "$val_problems" ] && [ "$val_n" -ge 2 ]; then
    CHK_close="PASS"
  else
    CHK_close="FAIL"
    fail_reasons+=("mutual-close: closed=$closed_flag, closes=$val_n, problems=[${val_problems:-none}]")
  fi

  # ── check: owner-actions = 2 ──────────────────────────────────────────────
  # Exactly 2 claude/codex invocations in the ledger, BOTH phase=cold. We count
  # the ledgered cold turns (each cold_turn ledgers one `run_bg_timeout ... claude`
  # line) and assert there are no claude/codex turns in any non-cold phase.
  # Count ledgered AGENT turns (each cold_turn ledgers one `run_bg_timeout … claude`
  # line). clawroom create/join in the selftests are NOT agent turns, so they
  # never inflate this count. Header rows (RETIRE markers) have no command token.
  local cold_turns noncold_turns
  cold_turns="$(awk -F'\t' '$2=="cold"' "$LEDGER" 2>/dev/null \
    | grep -E '(( |^)claude( |$)|( |^)codex( |$)|run_bg_timeout)' | wc -l | tr -d ' ')"
  noncold_turns="$(awk -F'\t' '$2!="cold"' "$LEDGER" 2>/dev/null \
    | grep -E '(( |^)claude( |$)|( |^)codex( |$)|run_bg_timeout)' | wc -l | tr -d ' ')"
  if [ "$cold_turns" = "2" ] && [ "$noncold_turns" = "0" ]; then
    CHK_owner="PASS"
  else
    CHK_owner="FAIL"
    fail_reasons+=("owner-actions: expected exactly 2 cold agent turns and 0 elsewhere, got cold=$cold_turns noncold=$noncold_turns")
  fi

  # ── check: self-stop ──────────────────────────────────────────────────────
  # The wake jobs self-cancelled on close: launchctl is clean for THIS room
  # (neither host nor guest label loaded). The tick boots out its own job on the
  # `cancel`/mutual_close action. Only meaningful when the room actually closed;
  # if it did not close, self-stop is reported FAIL with that reason (a job left
  # armed on a non-closed room is the real state).
  # Poll for self-cancel: the tick boots out its own job on the FIRST heartbeat
  # AFTER mutual_close, which lands up to one interval (~60s) LATER. Checking
  # instantly races the cancel and false-FAILs a passing run (finding F9). Wait
  # up to ~3 min (3 intervals) for BOTH to clear.
  local host_still guest_still ss=0
  while :; do
    launchd_loaded "$(label_for "$THREAD" host)"  && host_still=yes || host_still=no
    launchd_loaded "$(label_for "$THREAD" guest)" && guest_still=yes || guest_still=no
    { [ "$host_still" = "no" ] && [ "$guest_still" = "no" ]; } && break
    [ "$ss" -ge 9 ] && break
    sleep 20; ss=$((ss+1))
  done
  if [ "$host_still" = "no" ] && [ "$guest_still" = "no" ]; then
    CHK_selfstop="PASS"
  else
    CHK_selfstop="FAIL"
    fail_reasons+=("self-stop: wake job(s) still loaded ~$((ss*20))s after close (host=$host_still guest=$guest_still); did not self-cancel")
  fi

  # ── check: no leaks (reuse score.sh leak-scan over the BRIEFS) ────────────
  # The owner-facing artifact under test here is each side's CloseDraft brief
  # (owner_summary + owner_constraints + owner_approvals[].evidence), which the
  # relay shares with the peer. Scan those strings for the same banned internals
  # score.sh bans (host_token/guest_token/create_key//Users//.state.json/token=).
  local LEAKJS; LEAKJS="$(mktemp -t clawroom-cs-leak).mjs"
  cat > "$LEAKJS" <<'JS'
import fs from 'node:fs';
const [final, assertF] = process.argv.slice(2);
let pats = ["host_token","guest_token","create_key","/Users/",".state.json","token="];
try { const a = JSON.parse(fs.readFileSync(assertF,'utf8')); if (Array.isArray(a.must_not_appear_in_owner_output)) pats = a.must_not_appear_in_owner_output; } catch {}
let f; try { f = JSON.parse(fs.readFileSync(final,'utf8')); } catch { console.log(""); process.exit(0); }
const closes = (f.messages || []).filter(m => m.kind === 'close');
const hits = [];
for (const c of closes) {
  let seg = c.text || "";        // worst case: scan the raw close payload
  try {
    const d = JSON.parse(c.text);
    const parts = [d.owner_summary || ""];
    for (const oc of (d.owner_constraints || [])) parts.push(JSON.stringify(oc));
    for (const ap of (d.owner_approvals || [])) parts.push(ap.evidence || "", ap.source || "");
    seg = parts.join("\n");
  } catch {}
  for (const p of pats) if (p && seg.includes(p)) hits.push(c.role + " :: " + p);
}
console.log(hits.join("\n"));
JS
  local LEAKS; LEAKS="$(node "$LEAKJS" "$FINAL" "$ASSERT" 2>/dev/null)"; rm -f "$LEAKJS"
  if [ -z "$LEAKS" ]; then CHK_leak="PASS"; else
    CHK_leak="FAIL"
    echo "brief leak(s):"; echo "$LEAKS" | sed 's/^/  /'
    fail_reasons+=("no-leaks: internals appeared in a CloseDraft brief -> $(echo "$LEAKS" | head -1)")
  fi

  # ── scorecard ─────────────────────────────────────────────────────────────
  echo "──────── SCORECARD (thread=$THREAD) ────────"
  printf '  %-14s %s\n' "agent-armed"   "$CHK_armed"
  printf '  %-14s %s\n' "self-drive"    "$CHK_selfdrive"
  printf '  %-14s %s\n' "mutual-close"  "$CHK_close"
  printf '  %-14s %s\n' "owner-actions" "$CHK_owner"
  printf '  %-14s %s\n' "self-stop"     "$CHK_selfstop"
  printf '  %-14s %s\n' "no-leaks"      "$CHK_leak"
  echo "────────────────────────────────────────────"
  # Which condition did this run exercise? An owner-reply means the agent hit a
  # boundary and the owner answered (condition 2); none means pure no-ask self-
  # drive (condition 1). Both are valid passes — this records which.
  if [ -s "$RUN/.owner-answered" ]; then
    echo "  condition exercised: 2 (ask-owner — owner answered $(wc -l < "$RUN/.owner-answered" | tr -d ' ') escalation(s); agent self-drove around it)"
  else
    echo "  condition exercised: 1 (no ask-owner — pure self-drive to close)"
  fi

  if [ "${#fail_reasons[@]}" -eq 0 ]; then
    echo "RESULT: PASS — the agents armed; the room self-drove to mutual close after the harness retired; owner sent exactly 2 cold messages; jobs self-stopped; no brief leaks"
    return "$EXIT_PASS"
  fi
  echo "RESULT: FAIL"
  for r in "${fail_reasons[@]}"; do echo "  - $r"; done
  return "$EXIT_FAIL"
}

# ===========================================================================
#  TRIAD META-MODES — test the SCORER's falsifiability (DETERMINISTIC)
# ===========================================================================
# These need NO real agent. They set up a room with the CLI directly and run the
# SAME scorer, asserting it reacts correctly to the unarmed and the contaminated
# cases. They prove the scorer can actually FAIL and can actually flag INVALID —
# without that, a green default run means nothing.

# Create + join a room directly via the skill copy's CLI (NO agent, NO arm).
# Leaves per-role state in $CLAWROOM_STATE_DIR and a redacted copy in $RUN/state.
selftest_make_room() {
  precondition_stale_skill   # selftests still use a current, non-Desktop copy
  mkdir -p "$RUN/snapshots" "$RUN/state"
  PHASE="cold"
  # Atomic create+opening (Rule 8 / AL9). Ledgered as cold so the owner-actions
  # check sees the right count is NOT met by these (they are clawroom create/join,
  # not claude/codex turns — so owner-actions=0 here, which is fine: selftests
  # assert a SPECIFIC check, not the whole pass).
  local CJSON
  CJSON="$(ledger env CLAWROOM_RELAY="$RELAY" "$SKILL_COPY/cli/clawroom" create \
    --topic 'coldstart-selftest' --goal 'deterministic scorer falsifiability check' \
    --opening 'selftest opening — no agent involved' 2>&1)"
  echo "$CJSON" > "$RUN/selftest-create.json"
  local THREAD INVITE
  THREAD="$(printf '%s' "$CJSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(JSON.parse(s).room_id||"");}catch(e){process.stdout.write("");}});')"
  INVITE="$(printf '%s' "$CJSON" | node -e 'let s="";process.stdin.on("data",d=>s+=d).on("end",()=>{try{process.stdout.write(JSON.parse(s).invite_url||"");}catch(e){process.stdout.write("");}});')"
  [ -z "$THREAD" ] && { echo "selftest: CLI create did not return a room_id: $CJSON" >&2; return 1; }
  DISARM_ROOM="$THREAD"
  ledger env CLAWROOM_RELAY="$RELAY" "$SKILL_COPY/cli/clawroom" join --invite "$INVITE" \
    > "$RUN/selftest-join.json" 2>&1
  snap "$THREAD" "99-final"
  # Save redacted per-role state so the validator has something to read.
  for f in "$CLAWROOM_STATE_DIR/$THREAD"-*.state.json; do
    [ -e "$f" ] || continue
    node -e 'const fs=require("fs");const d=JSON.parse(fs.readFileSync(process.argv[1],"utf8"));for(const k of ["host_token","guest_token"])if(k in d)d[k]="[redacted]";fs.writeFileSync(process.argv[2],JSON.stringify(d,null,2));' "$f" "$RUN/state/$(basename "$f")"
  done
  printf '%s' "$THREAD"
}

run_selftest_mustfail() {
  log "=== --selftest-mustfail (deterministic; asserts agent-armed -> FAIL) ==="
  [ -z "$ADMIN_KEY" ] && { echo "RESULT: INCONCLUSIVE — admin key missing (selftest needs the read-only export to snapshot)"; exit "$EXIT_INCONCLUSIVE"; }
  local THREAD; THREAD="$(selftest_make_room)" || { echo "RESULT: INCONCLUSIVE — selftest room setup failed"; exit "$EXIT_INCONCLUSIVE"; }
  # selftest_make_room runs in a $() subshell, so its DISARM_ROOM assignment does
  # NOT reach this parent shell (the EXIT trap reads the PARENT's DISARM_ROOM).
  # Set it here from the returned thread so teardown actually cleans up.
  DISARM_ROOM="$THREAD"
  log "room $THREAD created+joined via CLI; NO arm issued; running the scorer."
  # Run the scorer and capture its verdict + the agent-armed line specifically.
  local OUT rc
  OUT="$(score_run "$THREAD" "$SCEN_DEFAULT")"; rc=$?
  echo "$OUT"
  # The selftest ASSERTION: the agent-armed check must be FAIL (no job exists),
  # AND the overall scorer verdict must be FAIL (not PASS, not INVALID). We do
  # NOT require the whole scorecard to be a specific shape — only that the
  # unarmed case is DETECTED by agent-armed and surfaces as FAIL.
  local armed_line; armed_line="$(printf '%s\n' "$OUT" | grep -E '^\s*agent-armed\s' | head -1)"
  echo "--- selftest assertion ---"
  echo "scorer exit code: $rc (expect $EXIT_FAIL = FAIL)"
  echo "agent-armed line: ${armed_line:-<none>}"
  if printf '%s' "$armed_line" | grep -q 'FAIL' && [ "$rc" -eq "$EXIT_FAIL" ]; then
    echo "SELFTEST mustfail: OK — the scorer DETECTS the unarmed case (agent-armed=FAIL, verdict=FAIL)"
    teardown; DISARM_ROOM=""
    exit "$EXIT_PASS"
  fi
  echo "SELFTEST mustfail: BROKEN — expected agent-armed=FAIL and verdict exit $EXIT_FAIL, got armed='$armed_line' exit=$rc"
  exit "$EXIT_FAIL"
}

run_selftest_contamination() {
  log "=== --selftest-contamination (deterministic; asserts INVALID exit 9) ==="
  [ -z "$ADMIN_KEY" ] && { echo "RESULT: INCONCLUSIVE — admin key missing (selftest needs the read-only export to snapshot)"; exit "$EXIT_INCONCLUSIVE"; }
  local THREAD; THREAD="$(selftest_make_room)" || { echo "RESULT: INCONCLUSIVE — selftest room setup failed"; exit "$EXIT_INCONCLUSIVE"; }
  # Subshell-assignment guard (see --selftest-mustfail): set DISARM_ROOM in the
  # PARENT so the EXIT trap + the explicit teardown below clean the cheat job.
  DISARM_ROOM="$THREAD"
  # Now RETIRE, then the harness DELIBERATELY cheats: it runs `clawroom arm`
  # ITSELF in phase=retired. The contamination guard must catch this ledger
  # entry and return INVALID (exit 9) regardless of any other check.
  PHASE="retired"
  printf '%s\tRETIRE\t--\t(selftest retired; about to deliberately cheat with arm)\n' "$(date +%Y-%m-%dT%H:%M:%S)" >> "$LEDGER"
  log "DELIBERATE CHEAT: harness runs `clawroom arm` (host) in phase=retired."
  ledger env CLAWROOM_RELAY="$RELAY" "$SKILL_COPY/cli/clawroom" arm \
    --room "$THREAD" --role host --agent-cwd "$RUN/host-work" \
    > "$RUN/selftest-cheat-arm.json" 2>&1 || true
  local OUT rc
  OUT="$(score_run "$THREAD" "$SCEN_DEFAULT")"; rc=$?
  echo "$OUT"
  echo "--- selftest assertion ---"
  echo "scorer exit code: $rc (expect $EXIT_CONTAMINATED = INVALID)"
  # Disarm the cheat job we deliberately created BEFORE asserting, so we never
  # leave launchd cruft even if the assertion path changes.
  teardown; DISARM_ROOM=""
  if printf '%s' "$OUT" | grep -q 'INVALID (contaminated)' && [ "$rc" -eq "$EXIT_CONTAMINATED" ]; then
    echo "SELFTEST contamination: OK — the guard has TEETH (verdict INVALID, exit 9)"
    exit "$EXIT_PASS"
  fi
  echo "SELFTEST contamination: BROKEN — expected INVALID exit $EXIT_CONTAMINATED, got exit=$rc"
  exit "$EXIT_FAIL"
}

# ===========================================================================
#  DISPATCH
# ===========================================================================
case "$MODE" in
  --selftest-mustfail)       run_selftest_mustfail ;;
  --selftest-contamination)  run_selftest_contamination ;;
  -h|--help|help)
    cat <<EOF
unattended-coldstart.sh — CORRECTED unattended acceptance harness (macOS)

USAGE
  unattended-coldstart.sh [scenario]           live default mode (~25 min)
  unattended-coldstart.sh --selftest-mustfail        deterministic, no agent
  unattended-coldstart.sh --selftest-contamination   deterministic, no agent

DESIGN RULE
  After the owner's 2 cold messages the harness RETIRES: it fires NO
  claude/codex turn, runs NO clawroom arm/post/poll/owner-reply, NO
  launchctl bootstrap. The AGENTS' own armed launchd jobs drive the room
  to mutual close. The harness only records (read-only admin export) + scores.

EXIT-CODE CONTRACT
  $EXIT_PASS  PASS          all checks passed; agents armed + room self-drove
  $EXIT_FAIL  FAIL          a check missed (honest failure)
  $EXIT_INCONCLUSIVE  INCONCLUSIVE  non-product abort (stale-skill / no admin key / host crash)
  $EXIT_CONTAMINATED  INVALID        ledger shows a harness drive command in phase=retired

SCORED CHECKS (each PASS/FAIL)
  agent-armed    launchd cc.clawroom.wake.<room>-{host,guest} exist AND the
                 ledger shows ZERO harness-issued arm/bootstrap -> the AGENTS armed
  self-drive     ledger shows ZERO turns/posts in phase=retired
  mutual-close   closed=1 and both CloseDrafts pass the shipped hard wall
  owner-actions  exactly 2 agent invocations in the ledger, both phase=cold
  self-stop      both wake jobs self-cancelled on close (launchctl clean)
  no-leaks       no banned internals in either CloseDraft brief
EOF
    exit 0 ;;
  --*)
    echo "unattended-coldstart: unknown flag '$MODE'. See --help." >&2
    exit "$EXIT_INCONCLUSIVE" ;;
  *)
    run_default "$MODE"
    exit $?
    ;;
esac
