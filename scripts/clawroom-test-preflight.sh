#!/usr/bin/env bash
# ClawRoom manual-test PRE-FLIGHT — run before every dogfood test.
#
# Why: the test only counts if each agent npx-installs the CURRENT build itself.
# A leftover local skill silently makes the run a stale-skill false test (rule
# 11). This wipes every clawroom an agent could load + the relocate cache, so a
# fresh agent is FORCED to npx-install, and prints what npx will pull.
#
# Usage:
#   bash scripts/clawroom-test-preflight.sh [TEST_PROJECT_DIR ...]
#   e.g. bash scripts/clawroom-test-preflight.sh ~/Desktop/project/clawroom-autoresearch-demo
#
# It does NOT touch ~/clawd (a separate project) or the repo source.
set -uo pipefail
REPO_VERSION="$(grep -m1 'version:' "$(cd "$(dirname "$0")/.." && pwd)/skill/SKILL.md" 2>/dev/null | tr -d ' ')"

echo "── 1. stop leftover monitoring ──────────────────────────────"
pkill -f 'clawroom watch' 2>/dev/null

echo "── 2. remove every clawroom skill an agent could load ───────"
rm -rf "$HOME/.claude/skills/clawroom" "$HOME/.agents/skills/clawroom" "$HOME/.clawroom/skill-runtime" 2>/dev/null
for rt in claude windsurf qoder factory kiro trae; do rm -rf "$HOME/.agents/skills/.$rt/skills/clawroom" 2>/dev/null; done
for proj in "$@"; do
  for rt in agents claude factory windsurf qoder kiro trae; do rm -rf "$proj/.$rt/skills/clawroom" 2>/dev/null; done
done

echo "── 3. clear npx skills-tool cache (force a fresh GitHub pull) ─"
find "$HOME/.npm/_npx" -maxdepth 2 -name skills -type d -exec rm -rf {} + 2>/dev/null

echo "── 4. VERIFY (all must be good) ─────────────────────────────"
echo "  ~/.claude/skills/clawroom (global) : $([ -e "$HOME/.claude/skills/clawroom" ] && echo 'PRESENT — BAD' || echo 'absent ✅')"
for proj in "$@"; do
  if [ -e "$proj/.claude/skills/clawroom" ] || [ -e "$proj/.agents/skills/clawroom" ]; then s='PRESENT — BAD'; else s='absent ✅'; fi
  echo "  $(basename "$proj") project clawroom        : $s"
done
GH="$(git ls-remote https://github.com/heyzgj/clawroom.git HEAD 2>/dev/null | cut -c1-12)"
echo "  npx will install (GitHub HEAD)     : $GH"
echo "  repo SKILL.md version (your build) : $REPO_VERSION"
echo
echo "Clean slate. Open your two agents and follow the SOP from Phase 1."
echo "After each agent installs, confirm its SKILL.md version == $REPO_VERSION before trusting the run."
