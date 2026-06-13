#!/usr/bin/env bash
# evals/e2e/scrub.sh — guarantee a clean-room before an E2E run.
#
# Requirement 1 (no stale clawroom anywhere) + Requirement 2 (zero memory
# of clawroom at session start) are enforced HERE, mechanically, not by
# operator diligence. We do NOT wipe $HOME (codex/claude auth lives there);
# we surgically remove every clawroom trace and then VERIFY clean, failing
# loud if anything survives.
#
# Usage: scrub.sh [--verify-only]
set -euo pipefail

VERIFY_ONLY="${1:-}"

# Every place a clawroom skill install or room state can hide.
TARGETS=(
  "$HOME/.agents/skills/clawroom"
  "$HOME/.claude/skills/clawroom"
  "$HOME/.codex/skills/clawroom"
  "$HOME/.config/agents/skills/clawroom"
  "$HOME/.clawroom-v4"
  "$HOME/.clawroom"
)

if [ "$VERIFY_ONLY" != "--verify-only" ]; then
  for t in "${TARGETS[@]}"; do
    [ -e "$t" ] && rm -rf "$t" && echo "scrubbed: $t" || true
  done
  # Catch ANY clawroom skill dir nested anywhere under the agent skill roots
  # (botched/nested prior installs like ~/.agents/skills/.agents/skills/...).
  for root in "$HOME/.agents" "$HOME/.claude" "$HOME/.codex" "$HOME/.config/agents"; do
    [ -d "$root" ] || continue
    find "$root" -type d -name clawroom 2>/dev/null \
      | while read -r d; do rm -rf "$d" && echo "scrubbed nested: $d"; done || true
  done
  # Any project-local installs under common work roots.
  find "$HOME/Desktop" "$HOME/tmp" /tmp -maxdepth 6 -type d -name clawroom -path '*/.agents/skills/*' 2>/dev/null \
    | while read -r d; do rm -rf "$d" && echo "scrubbed project-local: $d"; done || true
fi

# VERIFY: no clawroom SKILL.md, no v3/v4 state, anywhere an agent would look.
leaks=0
while IFS= read -r f; do
  echo "LEAK: $f"; leaks=$((leaks+1))
done < <(find "$HOME/.agents" "$HOME/.claude" "$HOME/.codex" "$HOME/.config" 2>/dev/null -path '*clawroom*' -name 'SKILL.md')
for s in "$HOME/.clawroom-v4" "$HOME/.clawroom"; do
  [ -e "$s" ] && { echo "LEAK: state dir $s"; leaks=$((leaks+1)); }
done

if [ "$leaks" -ne 0 ]; then
  echo "SCRUB FAILED: $leaks clawroom trace(s) survived — environment NOT clean." >&2
  exit 1
fi
echo "CLEAN: no clawroom skill or state anywhere an agent looks."
