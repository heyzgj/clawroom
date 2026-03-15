#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SRC_SKILL="$ROOT_DIR/skills/clawroom/SKILL.md"
DST_SKILL="$ROOT_DIR/apps/monitor/public/skill.md"

SRC_BRIDGE="$ROOT_DIR/skills/clawroom/scripts/openclaw_shell_bridge.sh"
DST_BRIDGE="$ROOT_DIR/apps/monitor/public/openclaw-shell-bridge.sh"
DST_BRIDGE_APP="$ROOT_DIR/apps/openclaw-bridge/scripts/openclaw_shell_bridge.sh"

if [[ ! -f "$SRC_SKILL" ]]; then
  echo "[sync] missing source skill: $SRC_SKILL" >&2
  exit 1
fi

if [[ ! -f "$SRC_BRIDGE" ]]; then
  echo "[sync] missing source bridge script: $SRC_BRIDGE" >&2
  exit 1
fi

install -m 0644 "$SRC_SKILL" "$DST_SKILL"
install -m 0755 "$SRC_BRIDGE" "$DST_BRIDGE"
install -m 0755 "$SRC_BRIDGE" "$DST_BRIDGE_APP"

echo "[sync] updated:"
echo "  - $DST_SKILL"
echo "  - $DST_BRIDGE"
echo "  - $DST_BRIDGE_APP"
