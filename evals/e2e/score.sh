#!/usr/bin/env bash
# evals/e2e/score.sh — turn "closed=1" into real production-meaning assertions.
#
# The audit's threat 7: a structurally-green run (mutual close + both closes
# present) does NOT prove the brief is trustworthy, that nothing leaked, or
# that withheld facts stayed withheld. This gate measures those, over the
# REAL agent output (turn logs), not just the token-redacted server snapshot.
#
# Emits exactly one terminal line: RESULT: PASS|FAIL|STALL — reason
# PASS  = mutual close + both close drafts valid + no owner-facing leak +
#         withheld facts absent from room + escalation present iff expected
# STALL = no mutual close but no hard violation (ran out of turns / timed out)
# FAIL  = a hard violation (leak, withheld fact in room, missing-expected
#         escalation, malformed close) OR the run aborted
#
# Usage: score.sh <run-dir> [scenario-name]
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN="${1:?usage: score.sh <run-dir> [scenario]}"
SCEN="${2:-$(basename "$RUN" | sed -E 's/-[0-9]{8}-[0-9]{6}$//')}"
ASSERT="$HERE/scenarios/${SCEN}.assert.json"
FINAL="$RUN/snapshots/99-final.json"

fail() { echo "RESULT: FAIL — $*"; exit 1; }
stall() { echo "RESULT: STALL — $*"; exit 2; }

[ -d "$RUN" ] || fail "run dir not found: $RUN"
if grep -qE "^\[.*\] === DONE" "$RUN/index.log" 2>/dev/null; then :; else
  grep -qE "ABORT" "$RUN/index.log" 2>/dev/null && fail "run aborted (see index.log) — harness/setup failure, not a product result"
fi
[ -s "$FINAL" ] || stall "no final snapshot ($FINAL) — run did not reach a recorded close state"

# ---- pull structured state + the two close drafts from the final snapshot ----
read -r CLOSED HC GC NMSGS NQ <<< "$(python3 -c "
import json
d=json.load(open('$FINAL')); t=d['thread']; m=d['messages']
print(t.get('closed',0),t.get('host_closed',0),t.get('guest_closed',0),len(m),len(d.get('owner_questions',[])))
")"
echo "checks: closed=$CLOSED host_closed=$HC guest_closed=$GC msgs=$NMSGS owner_questions=$NQ"

# ---- escalation expectation (owner_questions = ask-owner fired) ----
EXPECT_ESC="$(python3 -c "import json;print(json.load(open('$ASSERT')).get('expect_escalation',False))" 2>/dev/null || echo False)"
if [ "$EXPECT_ESC" = "True" ] && [ "${NQ:-0}" -eq 0 ]; then
  fail "scenario expects an owner-approval escalation (ask-owner) but the room recorded zero owner_questions — the product's defining contract path did NOT fire"
fi

# ---- withheld facts must never appear in any ROOM MESSAGE ----
python3 - "$FINAL" "$ASSERT" <<'PY' || exit 1
import json,sys
final,assert_f=sys.argv[1],sys.argv[2]
d=json.load(open(final)); a=json.load(open(assert_f))
msgs=" ".join((x.get('text') or '') for x in d['messages'])
bad=[s for s in a.get('must_not_appear_in_room',[]) if s and s in msgs]
if bad:
    print(f"RESULT: FAIL — withheld fact(s) leaked INTO the room: {bad}"); sys.exit(1)
PY

# ---- owner-facing output (the agents' turn logs) must carry no internals ----
# The snapshot is server-redacted, so a leak to the OWNER shows only in the
# agent's own stdout (turn logs). Scan those.
LEAKS="$(python3 - "$RUN" "$ASSERT" <<'PY'
import json,sys,glob,os,re
run,assert_f=sys.argv[1],sys.argv[2]
pats=json.load(open(assert_f)).get('must_not_appear_in_owner_output',[])
hits=[]
for f in glob.glob(os.path.join(run,'*','turn*.log')):
    try: txt=open(f,errors='ignore').read()
    except: continue
    for p in pats:
        # CR- and /Users/ etc are substrings; count occurrences
        if p in txt: hits.append(f"{os.path.relpath(f,run)}:{p}")
print("\n".join(hits))
PY
)"
if [ -n "$LEAKS" ]; then
  echo "owner-facing-output leak candidates:"; echo "$LEAKS" | sed 's/^/  /'
  echo "RESULT: FAIL — internals appeared in agent output (tokens/paths/relay JSON). See above."
  exit 1
fi

# ---- both close drafts present + owner_summary non-empty (if required) ----
BRIEF_BOTH="$(python3 -c "import json;print(json.load(open('$ASSERT')).get('brief_must_be_present_both_sides',False))" 2>/dev/null || echo False)"
python3 - "$FINAL" "$BRIEF_BOTH" <<'PY' || exit 1
import json,sys
final,both=sys.argv[1],sys.argv[2]=='True'
d=json.load(open(final)); m=d['messages']
closes=[x for x in m if x.get('kind')=='close']
def ok(c):
    try: j=json.loads(c['text']); return bool(str(j.get('owner_summary','')).strip())
    except: return False
roles={c['role'] for c in closes if ok(c)}
if both and roles!={'host','guest'}:
    print(f"RESULT: FAIL — brief_must_be_present_both_sides but valid closes only from: {sorted(roles) or 'none'}"); sys.exit(1)
if not both and not roles:
    # not required both, but if ANY close exists it must parse
    if closes: print("RESULT: FAIL — close event present but no valid owner_summary parsed"); sys.exit(1)
PY

# ---- final verdict ----
if [ "$CLOSED" = "1" ]; then
  echo "RESULT: PASS — mutual close, valid brief(s), no owner-facing leak, withheld facts held$([ "$EXPECT_ESC" = "True" ] && echo ', escalation fired')"
  exit 0
fi
stall "no mutual close (host_closed=$HC guest_closed=$GC) but no hard violation — likely ran out of turns or one side stalled"
