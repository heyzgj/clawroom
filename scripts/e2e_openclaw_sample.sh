#!/usr/bin/env bash
set -euo pipefail

BASE_URL=${BASE_URL:-http://127.0.0.1:8080}

if [[ -z "${ROOM_ID:-}" || -z "${INVITE_A:-}" || -z "${INVITE_B:-}" ]]; then
  echo "Set ROOM_ID, INVITE_A, INVITE_B first"
  exit 1
fi

export PYTHONPATH=apps/openclaw-bridge/src

uv run python -m openclaw_bridge.cli \
  --base-url "$BASE_URL" \
  --room-id "$ROOM_ID" \
  --token "$INVITE_A" \
  --agent-id main \
  --role initiator \
  --start \
  --thinking off \
  --print-result &

PID_A=$!

uv run python -m openclaw_bridge.cli \
  --base-url "$BASE_URL" \
  --room-id "$ROOM_ID" \
  --token "$INVITE_B" \
  --agent-id sam \
  --role responder \
  --thinking off \
  --print-result

wait "$PID_A"
