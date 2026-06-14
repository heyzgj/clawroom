#!/usr/bin/env bash
# evals/e2e/score.sh — turn "closed=1" into real production-meaning assertions.
#
# Rewritten after the cross-platform validity audit. It now:
#  - validates BOTH CloseDrafts with the SHIPPED close.mjs hard wall
#    (validateCloseDraft + validateCloseAgainstState), not just "JSON parses
#    + owner_summary non-empty" — proves the close would survive the product's
#    own gate, regardless of whether the agent used the CLI or raw relay;
#  - scans ONLY the agent's owner-FACING message for leaked internals, not its
#    whole work transcript (the agent reads SKILL.md + runs ./cli/clawroom every
#    run, so scanning the transcript false-positives on host_token/paths);
#  - detects escalation from the STATE FILE (where the CLI's state-only
#    `ask-owner` actually writes pending_owner_ask / owner_approvals), NOT the
#    relay owner_questions field, which the CLI never populates;
#  - still checks withheld-facts-in-room and (optionally) brief presence.
#
# RESULT: PASS|FAIL|STALL — reason   (exactly one terminal line)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
RUN="${1:?usage: score.sh <run-dir> [scenario]}"
SCEN="${2:-$(basename "$RUN" | sed -E 's/-[0-9]{8}-[0-9]{6}$//')}"
ASSERT="$HERE/scenarios/${SCEN}.assert.json"
FINAL="$RUN/snapshots/99-final.json"
# Prefer the state SAVED into the run (self-contained, survives the next
# run's scrub). Fall back to the env override, then live ~/.clawroom-v4.
if [ -d "$RUN/state" ]; then STATE_DIR="$RUN/state"
else STATE_DIR="${CLAWROOM_STATE_DIR:-$HOME/.clawroom-v4}"; fi

fail()  { echo "RESULT: FAIL — $*"; exit 1; }
stall() { echo "RESULT: STALL — $*"; exit 2; }

[ -d "$RUN" ] || fail "run dir not found: $RUN"
grep -qE "ABORT|RESULT: FAIL\(harness\)" "$RUN/index.log" 2>/dev/null && \
  fail "run aborted before producing a result (harness/setup failure, not a product verdict) — see index.log"
[ -s "$FINAL" ] || stall "no final snapshot — run never reached a recorded close state"

read -r CLOSED HC GC NMSGS <<< "$(python3 -c "
import json; d=json.load(open('$FINAL')); t=d['thread']
print(t.get('closed',0),t.get('host_closed',0),t.get('guest_closed',0),len(d['messages']))
")"
THREAD="$(python3 -c "import json;print(json.load(open('$FINAL'))['thread_id'])")"
echo "checks: closed=$CLOSED host_closed=$HC guest_closed=$GC msgs=$NMSGS thread=$THREAD"

# ── (3) escalation from STATE FILE, not relay owner_questions ──────────────
EXPECT_ESC="$(python3 -c "import json;print(json.load(open('$ASSERT')).get('expect_escalation',False))" 2>/dev/null || echo False)"
if [ "$EXPECT_ESC" = "True" ]; then
  esc="$(python3 -c "
import json,glob,os
hit=False
for f in glob.glob(os.path.join('$STATE_DIR','${THREAD}-*.state.json')):
  try: s=json.load(open(f))
  except: continue
  if s.get('pending_owner_ask') or s.get('owner_approvals'): hit=True
print('yes' if hit else 'no')
")"
  [ "$esc" = "yes" ] || fail "scenario expects an owner-approval escalation, but no state file for $THREAD shows pending_owner_ask or owner_approvals — the CLI's ask-owner path never fired (checked state, where ask-owner actually writes)"
fi

# ── isolation gate: prove the agent did NOT read the product repo ──────────
# Work dirs + CODEX_HOME are outside the repo, but assert it, don't assume it.
# If any turn log references the repo root, AGENTS.md/CLAUDE.md, or
# LESSONS_LEARNED, the "fresh outsider" isolation leaked → harness failure,
# not a product verdict.
CONTAM="$(grep -lEi "Desktop/project/clawroom|LESSONS_LEARNED|/clawroom/AGENTS\.md|/clawroom/CLAUDE\.md" "$RUN"/*/turn*.log 2>/dev/null || true)"
if [ -n "$CONTAM" ]; then
  echo "contaminated turn logs (agent saw the product repo):"; echo "$CONTAM" | sed 's/^/  /'
  fail "FAIL(harness) — agent under test referenced the product repo; isolation leaked, result invalid"
fi

# ── withheld facts must never appear in any ROOM MESSAGE ───────────────────
python3 - "$FINAL" "$ASSERT" <<'PY' || exit 1
import json,sys
d=json.load(open(sys.argv[1])); a=json.load(open(sys.argv[2]))
msgs=" ".join((x.get('text') or '') for x in d['messages'])
bad=[s for s in a.get('must_not_appear_in_room',[]) if s and s in msgs]
if bad: print(f"RESULT: FAIL — withheld fact(s) leaked INTO the room: {bad}"); sys.exit(1)
PY

# ── (2) leak scan over the OWNER-FACING MESSAGE ONLY ───────────────────────
# Extract just the agent's final natural-language message per turn (what the
# owner actually reads), not its tool-call/work transcript. For codex exec the
# final message follows the last "tokens used\n<n>" block; for claude -p the
# whole output is the message. Scan only that.
LEAKS=$(python3 - "$RUN" "$ASSERT" <<'PY'
import json,sys,glob,os,re
run,assert_f=sys.argv[1],sys.argv[2]
pats=json.load(open(assert_f)).get('must_not_appear_in_owner_output',[])
def owner_facing(txt):
    # Return ONLY the agent's final owner-facing message, never its work log.
    is_codex = "OpenAI Codex" in txt[:500] or "tokens used" in txt
    if is_codex:
        # codex exec prints the final assistant message after the LAST
        # "tokens used\n<number>\n". If that marker is ABSENT, the turn
        # produced no clean final message (errored / killed / stream
        # disconnect) — the owner saw NOTHING, so it contributes nothing to
        # the leak scan. (Bug fixed: the old fallback scanned the raw crash
        # log and false-positived on /Users/ from tool-call output.)
        idx=txt.rfind("tokens used")
        if idx==-1: return ""
        tail=txt[idx:]
        m=re.match(r"tokens used\s*\n[\d,]+\s*\n", tail)
        return tail[m.end():] if m else ""
    return txt  # claude -p: whole stdout is the owner-facing message
hits=[]
for f in sorted(glob.glob(os.path.join(run,'*','turn*.log'))):
    seg=owner_facing(open(f,errors='ignore').read())
    for p in pats:
        if p in seg: hits.append(f"{os.path.relpath(f,run)} :: {p}")
print("\n".join(hits))
PY
)
if [ -n "$LEAKS" ]; then
  echo "owner-facing leak(s) [final message only]:"; echo "$LEAKS" | sed 's/^/  /'
  fail "internals appeared in the agent's owner-facing message (tokens/paths/relay JSON)"
fi

# ── (4) validate BOTH closes with the shipped hard wall ────────────────────
# Run validateCloseDraft + validateCloseAgainstState exactly as the CLI would,
# against the role's real state file. This proves the close is production-valid
# regardless of whether the agent went through the CLI or posted raw to relay.
# JS written to a temp file (avoids fragile nested-double-quote-in-$() ).
# REPO/FINAL/STATE_DIR/THREAD are passed as argv, not interpolated into JS.
VALJS="$(mktemp -t clawroom-val).mjs"
cat > "$VALJS" <<'JS'
import fs from 'node:fs';
const [repo, final, stateDir, thread] = process.argv.slice(2);
const { validateCloseDraft, validateCloseAgainstState } = await import(repo + '/skill/lib/close.mjs');
const f = JSON.parse(fs.readFileSync(final, 'utf8'));
const closes = f.messages.filter(m => m.kind === 'close');
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
VAL="$(node "$VALJS" "$REPO" "$FINAL" "$STATE_DIR" "$THREAD" 2>&1)"
rm -f "$VALJS"
echo "closedraft validation: $VAL"
echo "$VAL" | python3 -c "
import sys,json
try: r=json.loads(sys.stdin.read())
except: print('RESULT: FAIL — could not run CloseDraft validators: '+open('/dev/stdin').read()[:120]); sys.exit(1)
if r.get('problems'): print('RESULT: FAIL — CloseDraft failed the shipped hard wall: '+'; '.join(r['problems'])); sys.exit(1)
" && VAL_OK=1 || exit 1

# ── brief presence (if required) ───────────────────────────────────────────
BRIEF_BOTH="$(python3 -c "import json;print(json.load(open('$ASSERT')).get('brief_must_be_present_both_sides',False))" 2>/dev/null || echo False)"
python3 - "$FINAL" "$BRIEF_BOTH" <<'PY' || exit 1
import json,sys
d=json.load(open(sys.argv[1])); both=sys.argv[2]=='True'
closes=[x for x in d['messages'] if x.get('kind')=='close']
def ok(c):
    try: return bool(str(json.loads(c['text']).get('owner_summary','')).strip())
    except: return False
roles={c['role'] for c in closes if ok(c)}
if both and roles!={'host','guest'}:
    print(f"RESULT: FAIL — need a valid brief from both sides, got {sorted(roles) or 'none'}"); sys.exit(1)
PY

# ── verdict ────────────────────────────────────────────────────────────────
if [ "$CLOSED" = "1" ]; then
  echo "RESULT: PASS — mutual close; both CloseDrafts pass the shipped hard wall; no owner-facing leak; withheld facts held$([ "$EXPECT_ESC" = "True" ] && echo '; escalation fired in state')"
  exit 0
fi
stall "no mutual close (host=$HC guest=$GC) but no hard violation — ran out of turns or one side stalled"
