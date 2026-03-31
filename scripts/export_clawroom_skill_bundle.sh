#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/skills/clawroom"
DEFAULT_OUTPUT_DIR="$ROOT_DIR/dist/clawroom-skill"
OUTPUT_DIR="${1:-$DEFAULT_OUTPUT_DIR}"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$TMP_DIR/agents" "$TMP_DIR/references" "$TMP_DIR/scripts"

cp "$SOURCE_DIR/SKILL.md" "$TMP_DIR/SKILL.md"
cp "$SOURCE_DIR/agents/openai.yaml" "$TMP_DIR/agents/openai.yaml"
cp "$SOURCE_DIR/references/api.md" "$TMP_DIR/references/api.md"
cp "$SOURCE_DIR/references/contacts-api.md" "$TMP_DIR/references/contacts-api.md"
cp "$SOURCE_DIR/references/managed-gateway.md" "$TMP_DIR/references/managed-gateway.md"
cp "$SOURCE_DIR/references/owner-context-schema.md" "$TMP_DIR/references/owner-context-schema.md"
cp "$SOURCE_DIR/scripts/clawroom_preflight.py" "$TMP_DIR/scripts/clawroom_preflight.py"
cp "$SOURCE_DIR/scripts/clawroom_owner_reply.py" "$TMP_DIR/scripts/clawroom_owner_reply.py"
cp "$SOURCE_DIR/scripts/clawroom_launch_participant.py" "$TMP_DIR/scripts/clawroom_launch_participant.py"
cp "$SOURCE_DIR/scripts/host_start_room.py" "$TMP_DIR/scripts/host_start_room.py"
cp "$SOURCE_DIR/scripts/room_poller.py" "$TMP_DIR/scripts/room_poller.py"
cp "$SOURCE_DIR/scripts/state_paths.py" "$TMP_DIR/scripts/state_paths.py"
cp "$SOURCE_DIR/scripts/openclaw_shell_bridge.sh" "$TMP_DIR/scripts/openclaw_shell_bridge.sh"

cat > "$TMP_DIR/README.md" <<'EOF'
# ClawRoom

Installable ClawRoom skill bundle for OpenClaw.

This bundle is built from the source skill in `skills/clawroom`.

## Default path

- OpenClaw runtime
- script execution available
- writable workspace for the ClawRoom state root
- `openclaw agent` supports `--session-id` and `--deliver`
- `scripts/host_start_room.py`
- `scripts/clawroom_launch_participant.py`
- bundled `room_poller.py`

Run preflight first:

```bash
python3 scripts/clawroom_preflight.py --json
```

If preflight returns `ready`, capture the state root first:

```bash
STATE_ROOT="$(python3 scripts/clawroom_preflight.py --print-state-root)"
```

Then create or join the room and hand off to `scripts/clawroom_launch_participant.py`.

## Install

From a published repo:

```bash
npx skills add heyzgj/clawroom
```

To install for a specific agent only when needed:

```bash
npx skills add heyzgj/clawroom -a codex -y
```

## Publish

```bash
clawhub publish . --slug clawroom --name "ClawRoom" --version 1.3.0 --tags latest
```
EOF

mkdir -p "$OUTPUT_DIR"
rsync -a --delete --exclude '.git' "$TMP_DIR"/ "$OUTPUT_DIR"/
echo "Exported ClawRoom bundle to $OUTPUT_DIR"
