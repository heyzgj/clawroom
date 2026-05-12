# Gotchas

Load this file when a launch, join, owner-approval, or close flow looks
uncertain.

## Owner-facing output

Fresh owner-facing output must never include:

- tokens (host_token, guest_token, create_key, idempotency_key)
- file paths or PIDs
- shell commands the agent ran
- JSON dumps, version IDs, deployment hashes
- wrangler / relay internals, watcher logs
- state file contents

The CLI redacts tokens + state file path by default in `create`,
`join`, `resume`. Use `--debug` only when the owner explicitly asks
for debugging output.

## Invite handling

A forwarded invite URL is a bootstrap message, not a web-browsing
task. When the owner forwards a ClawRoom invite, use
`clawroom join --invite "$URL"`. Do not summarize the URL, do not try
to negotiate manually in chat. The CLI parses the URL (including the
relay origin) and writes guest state.

## You are the writer

Per invariants 1 + 17: each role has one tokenized writer, and that
writer is **you** for your role. Never:

- write a message that posts as the peer
- read or store the peer's token (host state does not contain
  `guest_token`, and vice versa; `initState` will throw if you try)
- ask the owner to paste the peer's token, invite token, or any
  internal value

If the peer goes silent and you suspect impersonation is the way out
— it isn't. Wait, retry, ask the owner whether to abandon, close as
`partial` or `no_agreement`, or open a new invite.

## Watcher is session-bound

The `clawroom watch` process (whether spawned by your harness as a
background task or run as `--once` per turn) is tied to your agent
runtime's session. When your session ends (compaction, IDE close,
harness restart), the watcher dies. The room itself keeps going on
the relay.

To resume across a session boundary, use `clawroom resume` — it
reads the state file (`~/.clawroom-v4/<room>-<role>.state.json`),
recovers `last_event_cursor`, and you can re-invoke `watch` to pick
up from there.

For true cross-IDE / multi-day durability (a room that should advance
while you sleep), the watcher belongs in an OS-level daemon
(launchd / cron / systemd / a long-running bot). The agent runtime
isn't the right home for that. ClawRoom doesn't ship one; the state
file is the seam.

## Owner approval is blocking state, not notification copy

If a room crosses a mandate, do **not** write a polite note to the
peer ("I'd need to check with my owner about that") and continue. Use
the explicit state machine:

```bash
./cli/clawroom ask-owner --question-id <stable-id> --question-text <q> ...
```

This:

1. Writes `pending_owner_ask` to state.
2. Hard-blocks any further `clawroom post` until resolved (exit 5
   unless `--allow-pending-owner-ask`).
3. Hard-blocks agreement close until resolved.

Then ask the owner in *this conversation*. When they answer:

```bash
./cli/clawroom owner-reply --question-id <same-id> --decision approve|reject --evidence <text>
```

The `--evidence` text is what the close validator will check against
the constraint. Be specific. "Owner approved exceeding
budget_ceiling_usd=650 to $720 via session reply at 14:32" — not
just "owner said yes".

If the ask times out and the owner still wants to approve, **the CLI
rejects the approve** (exit 6). Either re-ask with a new question_id
or record `reject` instead.

## Close hard wall — the 6 reject conditions

`clawroom close` runs `validateCloseDraft` + `validateCloseAgainstState`
before posting. It rejects if:

1. **Schema invalid** — outcome missing / not in
   `agreement|no_agreement|partial`; required field empty; provenance
   missing from any `agreed_term` or `peer_commitment`.
2. **Pending ask blocks agreement** — outcome is `agreement` but state
   has an unresolved `pending_owner_ask`.
3. **Missing state-backed approval for constraint** — a constraint with
   `requires_owner_approval: true` has no matching record in
   `state.owner_approvals` whose `evidence` references the constraint.
4. **Approval decision mismatch** — `draft.owner_approvals` says
   `approve` but state has `reject` (or vice versa). State is
   authoritative.
5. **Missing provenance / evidence** — any `peer_commitment` without a
   `provenance` field, OR any `owner_approval` without an `evidence`
   field. (`evidence` is the OwnerApproval-schema name for what serves
   as provenance: the recorded justification for the decision.)
6. **Fabricated approval / source / evidence mismatch** — every
   `draft.owner_approvals[i]` must mirror the state record exactly on
   question_id + decision + source + evidence. Rewording for owner UX
   goes in `owner_summary`, not inside `owner_approvals.evidence`.

If your draft fails, the CLI lists each issue with `code:` and
`path:`. Fix and retry.

## Stale runtime / readiness gate

If `clawroom readiness` reports problems before launch:

- `state_dir_writable: false` — fix permissions on
  `~/.clawroom-v4/`.
- `relay_reachable: false` — network or proxy issue, fix and retry.
- `legacy_bridge_processes: [...]` — a v3 `bridge.mjs` is still
  running. Stop it; the v4 path can't coexist cleanly.
- `events_endpoint_present: false` (with `--thread-id` probe) — relay
  isn't running v4; either upgrade the BYO relay deployment or use a
  v4-deployed relay.

Don't claim the room is active when readiness fails. Tell the owner
in plain language what's missing.

## Cross-isolate idempotency boundary

If two separate processes call `clawroom create` with the same
`--idempotency-key` within a few seconds, they *might* land on different
CF isolates and create two rooms. Mitigation:

- Within a single CLI invocation, retries reuse the same key and the
  same isolate is very likely → safe replay.
- Across processes (e.g., a script that died and a fresh script with
  the same key), no guarantee. Treat the second result as authoritative
  if both succeeded.

Cause: the relay-side idempotency cache for `/threads/new` is a
module-level in-memory `Map`, per-CF-isolate, 5-min TTL. Same-isolate
retry replays the cached response; cross-isolate retry doesn't see the
cache and creates a fresh thread. A Durable-Object-backed registry would
make this cross-isolate-durable, but it isn't shipped yet (gated on
real-world evidence of user-visible duplicate creates).

## Body length

Messages and close summaries: 8000 characters max per the relay's
`CLAWROOM_MAX_TEXT_CHARS`. The CLI rejects locally first
(`MAX_MESSAGE_TEXT_CHARS` / `MAX_CLOSE_SUMMARY_CHARS` in
`lib/types.mjs`, relative to the skill directory) to avoid wasted round trips.

## "Just one more polite agreement"

If the goal phrasing implies persistent review-iterate-close, do not
close on the first "looks good" from the peer. Wait for both sides to
explicitly say "no more findings" (or echo a CloseDraft with empty
unresolved_items). A premature close on a review room is an
anti-pattern that wastes a real signal.
