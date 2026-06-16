#!/usr/bin/env bash
# Mark a ClawRoom unattended-dogfood run dir as SENSITIVE (it holds live room
# tokens in state/*.json and the invite secret in invite.txt) and emit a
# shareable, redacted bundle next to it. The run dir itself is NEVER shareable.
#
# Usage: scripts/redact-dogfood-run.sh <run-dir>
set -euo pipefail
RUN="${1:?usage: redact-dogfood-run.sh <run-dir>}"
[ -d "$RUN" ] || { echo "no such dir: $RUN" >&2; exit 1; }
RUN="${RUN%/}"

# 1. Lock down + label the raw dir (it is sensitive: tokens + invite).
chmod -R go-rwx "$RUN" 2>/dev/null || true
cat > "$RUN/SENSITIVE.md" <<EOF
# SENSITIVE — raw run dir, DO NOT SHARE
Holds live room tokens (state/*.json: host_token / guest_token) and the
invite secret (invite.txt: an /i/.../CR-... auth code). Share only the
redacted bundle: $(basename "$RUN").redacted/
EOF

# 2. Build the redacted bundle (logs + summary only; never state/ or invite.txt).
BUNDLE="$RUN.redacted"; rm -rf "$BUNDLE"; mkdir -p "$BUNDLE"
redact() {
  sed -E \
    -e 's#https?://[^ "]*/i/t_[a-z0-9-]+/CR-[A-Z0-9]+#[REDACTED_INVITE_URL]#g' \
    -e 's/CR-[A-Z0-9]{6,}/CR-[REDACTED]/g' \
    -e 's/(host|guest)_[a-f0-9]{16,}/[REDACTED_TOKEN]/g' \
    -e 's/("?(host_token|guest_token)"?[[:space:]]*[:=][[:space:]]*"?)[A-Za-z0-9_-]+/\1[REDACTED]/g'
}
for f in PROOF-SUMMARY.md wakeup-host.log wakeup-guest.log host-cold.log guest-cold.log; do
  [ -f "$RUN/$f" ] && redact < "$RUN/$f" > "$BUNDLE/$f"
done

# 3. Fail closed if any token / invite secret survived into the bundle.
LEAK="$(grep -rElE 'host_[a-f0-9]{16}|guest_[a-f0-9]{16}|/i/t_[a-z0-9-]+/CR-[A-Z0-9]|CR-[A-Z0-9]{6}' "$BUNDLE" 2>/dev/null || true)"
if [ -n "$LEAK" ]; then echo "REFUSING: secret leaked into bundle: $LEAK" >&2; exit 2; fi
echo "raw (SENSITIVE): $RUN"
echo "shareable bundle: $BUNDLE"
ls -1 "$BUNDLE" | sed 's/^/  /'
echo "verified: no token/invite leaks in bundle"
