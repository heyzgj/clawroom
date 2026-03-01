#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDGE_DIR="$ROOT_DIR/apps/edge"
MONITOR_DIR="$ROOT_DIR/apps/monitor"
PAGES_PROJECT="${CLAWROOM_PAGES_PROJECT:-clawroom-monitor}"

echo "[clawroom] root: $ROOT_DIR"

echo "[clawroom] checking wrangler auth..."
if ! (cd "$EDGE_DIR" && npx wrangler whoami >/dev/null 2>&1); then
  echo "[clawroom] not authenticated. run: cd $EDGE_DIR && npx wrangler login"
  exit 1
fi

echo "[clawroom] deploying API worker..."
(cd "$EDGE_DIR" && npm run deploy)

echo "[clawroom] ensuring Pages project exists: $PAGES_PROJECT"
if ! (cd "$MONITOR_DIR" && npx wrangler pages project create "$PAGES_PROJECT" --production-branch main >/dev/null 2>&1); then
  echo "[clawroom] pages project may already exist; continuing."
fi

echo "[clawroom] building monitor..."
(cd "$MONITOR_DIR" && npm run build)

echo "[clawroom] deploying monitor pages..."
(cd "$MONITOR_DIR" && npx wrangler pages deploy dist --project-name "$PAGES_PROJECT")

echo
echo "[clawroom] deploy completed."
echo "[clawroom] recommended domain layout:"
echo "  - API Worker: api.clawroom.cc"
echo "  - Monitor UI: clawroom.cc"
echo "[clawroom] if needed, add worker route in apps/edge/wrangler.toml and redeploy."

