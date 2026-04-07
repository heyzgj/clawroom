#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BASE_URL="${CLAWROOM_API_BASE:-http://127.0.0.1:8787}"
WORKSPACE_ROOT="${WORKSPACE_ROOT:-$ROOT/.tmp/autoresearch_sync_demo}"
STATE_DIR="$WORKSPACE_ROOT/state"
A1_DIR="$WORKSPACE_ROOT/a1"
A2_DIR="$WORKSPACE_ROOT/a2"
FIXTURES_DIR="$ROOT/scripts/autoresearch_sync_demo/fixtures"
A1_SUMMARY_FILE="${A1_SUMMARY_FILE:-$FIXTURES_DIR/a1_cycle1_summary.txt}"
A2_SUMMARY_FILE="${A2_SUMMARY_FILE:-$FIXTURES_DIR/a2_cycle1_summary.txt}"
TOPIC="${TOPIC:-autoresearch sync - dry run cycle 1}"
GOAL="${GOAL:-Share findings, mark dead ends, and split the next exploration direction without duplicate work.}"

usage() {
  cat <<USAGE
Usage: $(basename "$0")

Environment overrides:
  CLAWROOM_API_BASE   API base URL (default: http://127.0.0.1:8787)
  WORKSPACE_ROOT      Workspace root for the fake cycle
  A1_SUMMARY_FILE     Summary fixture for agent A1
  A2_SUMMARY_FILE     Summary fixture for agent A2
  TOPIC               Room topic override
  GOAL                Room goal override

This script prepares a single fake coordinated cycle:
  1. seeds two workspace directories
  2. creates one real ClawRoom sync room
  3. writes phase-specific prompt files for Claude/Codex to participate in the room
  4. prints the exact next commands to run
USAGE
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

mkdir -p "$STATE_DIR" "$A1_DIR" "$A2_DIR"

seed_program_md() {
  local target="$1"
  if [[ ! -f "$target/program.md" ]]; then
    cat >"$target/program.md" <<'PROGRAM'
# Autoresearch Program

Make one small, testable change per experiment. Keep notes crisp. Prefer disciplined exploration over noisy thrash.
PROGRAM
  fi
}

seed_program_md "$A1_DIR"
seed_program_md "$A2_DIR"

ROOM_JSON="$STATE_DIR/room_create_cycle1.json"
python3 "$ROOT/scripts/autoresearch_sync_demo/orchestrator.py" \
  --base-url "$BASE_URL" \
  create-room \
  --topic "$TOPIC" \
  --goal "$GOAL" >"$ROOM_JSON"

ROOM_EXPORTS="$STATE_DIR/room_exports.sh"
python3 - "$ROOM_JSON" >"$ROOM_EXPORTS" <<'PY'
import json
import shlex
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text())
room_id = payload["room"]["id"]
host_token = payload["host_token"]
token_a1 = payload["invites"]["agent_a1"]
token_a2 = payload["invites"]["agent_a2"]
print(f"export ROOM_ID={shlex.quote(room_id)}")
print(f"export HOST_TOKEN={shlex.quote(host_token)}")
print(f"export TOKEN_A1={shlex.quote(token_a1)}")
print(f"export TOKEN_A2={shlex.quote(token_a2)}")
PY
source "$ROOM_EXPORTS"

A1_PHASE1_PROMPT="$STATE_DIR/a1_phase1_prompt.txt"
A2_PHASE1_PROMPT="$STATE_DIR/a2_phase1_prompt.txt"
A1_PHASE2_PROMPT="$STATE_DIR/a1_phase2_prompt.txt"
A2_PHASE2_PROMPT="$STATE_DIR/a2_phase2_prompt.txt"
SYNC_PY="$ROOT/scripts/autoresearch_sync_demo/sync.py"

cat >"$A1_PHASE1_PROMPT" <<EOF2
You are agent_a1 in a fake autoresearch sync dry run.

This is a single bounded task, not an open-ended session.
This is Phase 1 only.
Your job is only to join the room, sync your local research state, send one substantive Phase 1 message, and stop.
Do not try to close the room in this call.

Your local workspace is:
$A1_DIR

Your local summary file is:
$A1_SUMMARY_FILE

The ClawRoom sync script is:
$SYNC_PY

Room details:
- room_id: $ROOM_ID
- token: $TOKEN_A1

Do this:
1. Read $A1_SUMMARY_FILE and your local program.md.
2. Join the room with your summary:
   SUMMARY=\$(cat "$A1_SUMMARY_FILE")
   python3 "$SYNC_PY" --base-url "$BASE_URL" join --room-id "$ROOM_ID" --token="$TOKEN_A1" --client-name "a1-sync" --summary "\$SUMMARY"
3. Read the room:
   python3 "$SYNC_PY" --base-url "$BASE_URL" read --room-id "$ROOM_ID" --token="$TOKEN_A1"
4. Send exactly one substantive Phase 1 message with --text. Do not fill required fields in this call.
5. Your message should cover:
   - explored directions
   - current best result
   - a real dead end
   - what still looks uncertain but worth exploring
6. Stop immediately after that one substantive message.
7. Do not send DONE in this call.
8. Do not browse the web. Do not look for extra environment variables. Use the exact room_id and token shown above.
EOF2

cat >"$A2_PHASE1_PROMPT" <<EOF2
You are agent_a2 in a fake autoresearch sync dry run.

This is a single bounded task, not an open-ended session.
This is Phase 1 only.
Your job is only to join the room, read A1's Phase 1 note, send one substantive Phase 1 response, and stop.
Do not try to close the room in this call.

Your local workspace is:
$A2_DIR

Your local summary file is:
$A2_SUMMARY_FILE

The ClawRoom sync script is:
$SYNC_PY

Room details:
- room_id: $ROOM_ID
- token: $TOKEN_A2

Do this:
1. Read $A2_SUMMARY_FILE and your local program.md.
2. Join the room with your summary:
   SUMMARY=\$(cat "$A2_SUMMARY_FILE")
   python3 "$SYNC_PY" --base-url "$BASE_URL" join --room-id "$ROOM_ID" --token="$TOKEN_A2" --client-name "a2-sync" --summary "\$SUMMARY"
3. Read the room:
   python3 "$SYNC_PY" --base-url "$BASE_URL" read --room-id "$ROOM_ID" --token="$TOKEN_A2"
4. Send exactly one substantive Phase 1 response with --text. Do not fill required fields in this call.
5. Your message should cover:
   - explored directions
   - current best result
   - a real dead end
   - where you disagree with A1's framing, if needed
6. Stop immediately after that one substantive message.
7. Do not send DONE in this call.
8. Do not browse the web. Do not look for extra environment variables. Use the exact room_id and token shown above.
EOF2

cat >"$A1_PHASE2_PROMPT" <<EOF2
You are agent_a1 in a fake autoresearch sync dry run.

This is a single bounded task, not an open-ended session.
This is Phase 2 only.
Your job is to read the already-synced room state, send one convergence message with all flat required fields filled, then send DONE, then stop.

Your local workspace is:
$A1_DIR

The ClawRoom sync script is:
$SYNC_PY

Room details:
- room_id: $ROOM_ID
- token: $TOKEN_A1

Do this:
1. Read your local program.md.
2. Read the room:
   python3 "$SYNC_PY" --base-url "$BASE_URL" read --room-id "$ROOM_ID" --token="$TOKEN_A1"
3. Assume both Phase 1 messages are already present.
4. Send exactly one convergence message that:
   - names the shared current best basin
   - names the merged dead ends
   - keeps A1 on optimizer / learning-rate / warmup refinement
   - keeps A2 on dropout / weight decay / regularization refinement
5. In that same convergence message, fill all four required fields using flat strings:
   - best_result_summary=one sentence with concrete metric/settings
   - dead_ends_summary=a semicolon-separated list of the concrete dead ends you actually trust; if none are confirmed yet, say so explicitly
   - assignment_a1=focus: ...; constraints: ...
   - assignment_a2=focus: ...; constraints: ...
6. Immediately after that message, send:
   python3 "$SYNC_PY" --base-url "$BASE_URL" send --room-id "$ROOM_ID" --token="$TOKEN_A1" --intent DONE --text "DONE"
7. Stop.

Rules:
- Do not ask questions.
- Do not wait.
- Do not browse.
- Do not invent nested JSON.
- Use only flat string fills.
- Make the fields directly reusable for the next cycle. Avoid vague wording like "keep refining" without saying what to hold fixed.
- Do not invent extra dead ends just to make the list longer.
- If there are no special constraints, still write \"constraints:\" with a truthful minimal constraint.
EOF2

cat >"$A2_PHASE2_PROMPT" <<EOF2
You are agent_a2 in a fake autoresearch sync dry run.

This is a single bounded task, not an open-ended session.
This is Phase 2 only.
Your job is to read the room, confirm a reasonable convergence proposal if the four required fields are already present, then send DONE, then stop.

Your local workspace is:
$A2_DIR

The ClawRoom sync script is:
$SYNC_PY

Room details:
- room_id: $ROOM_ID
- token: $TOKEN_A2

Do this:
1. Read your local program.md.
2. Read the room:
   python3 "$SYNC_PY" --base-url "$BASE_URL" read --room-id "$ROOM_ID" --token="$TOKEN_A2"
3. If A1 has already filled:
   - best_result_summary
   - dead_ends_summary
   - assignment_a1
   - assignment_a2
   then send exactly one confirmation message accepting the split unless there is an obvious contradiction.
4. Immediately after that, send:
   python3 "$SYNC_PY" --base-url "$BASE_URL" send --room-id "$ROOM_ID" --token="$TOKEN_A2" --intent DONE --text "DONE"
5. Stop.

If the four fields are not present yet, send one short readiness message and stop without DONE.

Rules:
- Do not ask questions.
- Do not wait in a loop.
- Do not browse.
- Do not expand scope.
- Treat the proposal as acceptable only if assignment_a1 and assignment_a2 each contain both a focus and constraints, and dead_ends_summary names only dead ends that are actually supported by the discussion.
EOF2

cat <<EOF2

Prepared one fake coordinated sync cycle.

Workspace root:
  $WORKSPACE_ROOT

Room:
  room_id=$ROOM_ID
  host_token=$HOST_TOKEN

Prompt files:
  A1 phase 1 -> $A1_PHASE1_PROMPT
  A2 phase 1 -> $A2_PHASE1_PROMPT
  A1 phase 2 -> $A1_PHASE2_PROMPT
  A2 phase 2 -> $A2_PHASE2_PROMPT

Suggested next commands:
  claude -p "\$(cat \"$A1_PHASE1_PROMPT\")" --allowedTools bash,edit --permission-mode dontAsk --output-format text
  codex exec "\$(cat \"$A2_PHASE1_PROMPT\")"
  claude -p "\$(cat \"$A1_PHASE2_PROMPT\")" --allowedTools bash,edit --permission-mode dontAsk --output-format text
  codex exec "\$(cat \"$A2_PHASE2_PROMPT\")"

After both phase-2 commands finish and the room closes:
  python3 "$ROOT/scripts/autoresearch_sync_demo/orchestrator.py" --base-url "$BASE_URL" wait-close --room-id "$ROOM_ID" --host-token="$HOST_TOKEN"
  python3 "$ROOT/scripts/autoresearch_sync_demo/orchestrator.py" --base-url "$BASE_URL" apply-assignments --room-id "$ROOM_ID" --host-token="$HOST_TOKEN" --a1-dir "$A1_DIR" --a2-dir "$A2_DIR"

EOF2
