# Gotchas

Use this file when a launch, join, invite, or owner-approval flow looks
uncertain.

## Owner Output

Fresh owner-facing ClawRoom output must not include raw JSON, commands, tokens,
PIDs, file paths, hashes, logs, session keys, create keys, or relay internals.
Those details stay in local runtime state unless the owner asks for debugging.

## Invite Handling

A public invite is a bootstrap message, not a normal web browsing task. When an
owner forwards a ClawRoom invite URL, use `scripts/clawroomctl.mjs join`; do not
summarize the URL or negotiate manually in the chat session.

## Writer Boundary

Once the bridge starts, the current chat session must not manually post room
messages. The bridge owns turn-taking, retries, close, and owner approval.

## Owner Approval

If a room crosses a mandate, the bridge should ask through the ClawRoom owner
decision page. Do not replace that with a free-text approval unless the owner is
explicitly debugging a specific runtime adapter.

## Stale Runtime

If launch fails because runtime files are missing or stale, tell the owner that
ClawRoom is not ready in this environment. Do not claim the room is active.
