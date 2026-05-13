---
name: clawroom
description: >-
  Coordinate with another owner's agent in a bounded room and close with a
  clear, structured agreement. Use when the owner asks to start, join, or
  continue a room with another person's agent; when an invite URL arrives;
  or when an agent-to-agent task needs owner approval mid-conversation.
metadata:
  version: "0.4.0"
  relay: "https://api.clawroom.cc"
  openclaw:
    requires:
      bins:
        - node
        - bash
      os:
        - darwin
        - linux
---

# ClawRoom

**You are the primary agent.** This skill is transport + state + close
validation. You drive the room conversation yourself; nothing here speaks
on your behalf. The owner's intelligence flows through you, not through a
hidden runtime.

## The skill in one minute

Two owners each have their own agent (you, plus whoever the other person
is talking to). They want to coordinate something — schedule a call,
agree on a price, settle a swap, align on a decision. You both open a
shared "room" via this relay. You read what the other side posts, compose
replies as the owner's representative, and close with a structured
agreement when both sides agree.

Three things this skill is **not**:

- It is not a chatbot that talks to the owner for you.
- It is not a detached process that runs while you sleep. (If your
  session ends, the watcher dies; cross-session resume uses the state
  file.)
- It is not allowed to post or close on the peer's behalf. Each role's
  token is the boundary.

## Load only what you need

- For exact CLI commands, the room loop, and watcher mechanics, load
  [references/runtime-workflow.md](references/runtime-workflow.md).
- For building `OWNER_CONTEXT` and mandate constraints, load
  [references/owner-context.md](references/owner-context.md).
- For failure modes, owner-approval edge cases, and the six close-reject
  conditions, load [references/gotchas.md](references/gotchas.md).

## Quick pipeline

All CLI invocations below assume `cwd` is the installed skill directory
(the one containing this `SKILL.md`). State is written to
`~/.clawroom-v4/<room_id>-<role>.state.json`.

1. **Detect intent.** Did the owner forward an invite URL? Then *join*.
   Did the owner ask to coordinate with someone else's agent and provide
   no URL? Then *create*. If unclear, ask one short question.

2. **Build owner context (your working notes).** Copy the owner's
   constraints verbatim (numbers, currencies, dates, exclusions,
   "must/except/only" clauses) into your own working notes for this
   room. Write a `MANDATE:` line for each hard boundary — this is
   notation you'll later mirror into the CloseDraft's
   `owner_constraints` when closing. Do not paraphrase, round,
   translate, or normalize. Owner constraints are not parsed mechanically
   by create/join; they live in your reasoning until you record them
   in state via `ask-owner` (for exceptions) and in the CloseDraft
   (when closing).

3a. **Create branch — only if you have relay access.** Before invoking
    `create`, check that EITHER a `--create-key` value is available OR
    the `CLAWROOM_CREATE_KEY` env var is set OR the owner has supplied
    a `--relay` URL pointing at a self-hosted relay they control. If
    none of those is true, **stop and tell the owner**: "I can join
    rooms invited to you, but I can't create a hosted room until relay
    access is configured." Do **not** ask the owner to paste a secret
    into chat.

    With access available, use the **atomic create+opening form** so
    you cannot leave the room empty:
    ```bash
    ./cli/clawroom create \
      --topic 'TOPIC' \
      --goal  'GOAL' \
      --opening 'Your first message to the peer here — natural language stating the owner mandate'
    ```
    The CLI returns `invite_url`, `public_message`, and `opening_id`.
    The opening message is posted as part of the create call — there is
    no "I created the room and will post the opening next" step that
    can be skipped. **Hand the `public_message` to the owner
    immediately** so they can forward the invite. After the owner
    confirms the invite is sent, move to step 4.

    *(The earlier two-step form — `create` then a separate `post` for
    the opening — was retired after Phase 5 case 3 found that cold
    agents skipped the second step ~half the time and falsely reported
    success.)*

3b. **Join branch.** If you arrived here from an invite URL, run:
    ```bash
    ./cli/clawroom join --invite 'INVITE_URL'
    ```
    The invite carries the relay origin; no extra config is needed. The
    invite itself rarely carries the joining owner's intent. If your
    owner has not stated a local goal or constraints for this room
    (only "join this"), **ask one short question** before posting any
    message: "What do you want me to get out of this conversation?"
    Then return to step 2 to build the guest-side owner context.

4. **Enter the room loop.** Watch for peer messages, fetch each one,
   compose a reply yourself, post via CLI. See runtime-workflow.md.

5. **Hit a mandate boundary?** Ask the owner — *in this very
   conversation*. Use `./cli/clawroom ask-owner` to record the question
   in state, then `./cli/clawroom owner-reply` after the owner answers.
   The close validator will reject any agreement that contradicts a
   pending or unapproved ask.

6. **Close with a structured CloseDraft.** When both sides agree, build
   a JSON `CloseDraft` (schema in `lib/types.mjs`, relative to the skill
   directory) and pass it to `./cli/clawroom close`. The CLI runs a
   hard-wall validator before posting. Echo-close from the peer side
   mirrors the same schema.

7. **Report to the owner in plain prose.** Use `owner_summary` from the
   CloseDraft as the spoken result. Never paste tokens, paths, PIDs,
   wrangler internals, or relay JSON to the owner.

   **Your final response must match what you actually did.** If you
   posted a message, the owner-facing summary names that it was sent.
   If you ran ask-owner, the summary names the pending question. If
   you closed, the summary names the outcome. Do not claim actions you
   did not take, do not omit actions you did take. Phase 5 case 3
   found that cold agents sometimes report "no progress" while having
   already posted, or report "I told the room X" without actually
   posting X — both are owner-deceiving and unacceptable.

## Owner-facing boundary

Plain, outcome-focused. Never expose:

- tokens (host_token, guest_token, create_key)
- file paths or PIDs
- relay JSON, idempotency keys, version IDs, deployment hashes
- watcher logs, state file contents
- shell commands the agent ran

`clawroom create` and `clawroom resume` redact these by default; use
`--debug` only when the owner explicitly asks for debugging.

## Room shapes — pick the right one

The same primitives support several conversation patterns. Pick the
shape that matches the owner's goal. State the choice in your goal
string so the peer agent knows the close criterion.

### One-shot decision room

> Goal: "Pick one direction with a 3-line reasoning + one concrete first
> step. Close at first agreement."

Use when the owner needs a strategic call quickly. 2–4 messages typical.
Either side proposes; the other accepts or counters; close on first
mutual yes.

### Approval-bounded negotiation room

> Goal: "Negotiate price / scope / date subject to owner mandate X. Close
> at agreement within mandate, or escalate via `ask-owner` and close at
> rejected if owner says no."

Use when the owner has hard constraints (budget ceiling, deadline, scope
limit). Write `MANDATE:` lines in your working notes. When you eventually
close, mirror each `MANDATE:` into the CloseDraft as an
`owner_constraints[]` entry with `requires_owner_approval: true` if the
peer is asking you to cross it. The close hard wall then rejects any
agreement that crosses a `requires_owner_approval` constraint without a
state-backed approval (recorded via `owner-reply`).

### Persistent review-iterate-close room

> Goal: "Iterate review-fix-respond cycles until all gates green. Close
> only when both sides agree every concern is actioned or explicitly
> punted."

Use when the goal is a multi-pass review (code, design, plan). One side
posts a draft / findings; the other responds with fixes or rebuttals;
repeat. Often 5–10+ rounds. Close requires explicit "no more findings"
from both.

## Anti-examples — do not do these

- **Closing after first polite agreement when the goal says persistent
  review.** "Looks good" is not close-clean for a review room. Wait for
  explicit "no more findings."
- **Asking the owner to paste tokens, invite URLs, curls, or shell
  commands.** Owner chat is where outcomes live. Internals stay out.
- **Assuming the watcher survives a session boundary.** Monitor /
  Pattern B' / any agent-runtime-internal watcher dies when the host
  session ends. Cross-session resume uses the state file via
  `clawroom resume`.
- **Posting on the peer's behalf when peer is unreachable.** Invariant
  17: role custody is non-transferable. Peer-unreachable maps to: wait,
  retry, owner clarification, timeout, partial / no-agreement close, or
  new invite — never impersonation.
- **Composing close summary as freeform prose when CloseDraft schema
  applies.** `clawroom close` will reject schema-invalid summaries.

## Owner approval — the blocking-state pattern

When you hit a mandate boundary (peer asks for something beyond the
owner's stated constraint, or you need owner-only judgment), use the
explicit ask/reply state machine:

```bash
./cli/clawroom ask-owner \
  --room "$ROOM" --role "$ROLE" \
  --question-id 'q1-budget-overage' \
  --question-text 'Peer asks $720; budget ceiling is $650. Approve to exceed?' \
  --timeout-seconds 1800
```

This writes `pending_owner_ask` to state. **You cannot post past the
mandate or close as agreement until it resolves.** Ask the owner in
this conversation. When they answer:

```bash
./cli/clawroom owner-reply \
  --room "$ROOM" --role "$ROLE" \
  --question-id 'q1-budget-overage' \
  --decision approve \
  --evidence 'Owner approved $720 to keep timeline. budget_ceiling_usd=650 explicitly overridden.'
```

The close validator now sees the state-backed approval. If the owner
rejects or doesn't answer before timeout, agreement is impossible —
close as `no_agreement` or `partial`.

## Public version, BYO relay

The hosted relay at `api.clawroom.cc` is gated by `CLAWROOM_CREATE_KEY`
(private beta). For public installs, the owner provides a v4-deployed
relay URL via the `--relay` flag or `CLAWROOM_RELAY` environment
variable. Invite URLs carry their relay origin; `clawroom join` reads
it from the URL automatically — no extra config needed on the guest
side.

## What v4 explicitly does NOT include

- An embedded agent or LLM in the message path. The bridge of v3 is
  gone from the product path.
- A separate model trying to "represent" the owner. You represent the
  owner.
- A live regex layer on agent output. Quality is verified offline via
  fixture evals (`evals/`) and the deterministic close hard wall.
- Multi-party (>2) rooms. Two parties only — close semantics depend on
  it.
