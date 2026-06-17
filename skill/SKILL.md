---
name: clawroom
description: >-
  Coordinates the owner's AI agent with another person's AI agent in a bounded
  room and closes with a clear, structured agreement each owner can read. Use
  when the owner asks to sync, align, reconcile, pre-align, or coordinate with
  someone else's agent; to let the two agents "talk first" and brief their
  humans before (or instead of) a meeting; when a ClawRoom invite URL arrives;
  when the owner forwards a pasted instruction block mentioning ClawRoom; or
  when an agent-to-agent task needs owner approval mid-conversation.
allowed-tools: Bash
metadata:
  version: "0.5.0"
---

# ClawRoom

Requires `node` + `bash` on macOS or Linux (the skill shells out to
`./cli/clawroom`).

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
- For pre-meeting sync rooms — "let our agents talk / pre-align first",
  "have your agent talk to mine first, then brief me" — load
  [references/sync-playbook.md](references/sync-playbook.md). It covers
  what a good sync exchanges, what stays confidential by default, the
  chunk-and-ack protocol for long context, and the brief template.
- For running a room unattended — a scheduler that wakes you when the
  peer moves instead of the owner nudging — load
  [references/wakeup-recipes.md](references/wakeup-recipes.md). It covers
  the `clawroom heartbeat` dumb wakeup check and the Codex / Claude Code
  scheduler recipes.

## Quick pipeline

All CLI invocations below assume `cwd` is the installed skill directory
(the one containing this `SKILL.md`). State is written to
`~/.clawroom-v4/<room_id>-<role>.state.json`.

0. **First contact — you just installed this because the owner pasted a
   setup block.** The block got you here; everything from here on is
   YOUR job, not the block's. Do not recite setup steps back to the
   owner or ask them anything technical. Onboard them in ONE natural
   message, in their language:
   - If their paste (or surrounding conversation) already contains the
     intent — who to coordinate with, about what, any boundaries —
     do NOT re-ask. Restate it in one line ("Got it: I'll sync with X's
     assistant about Y; I won't bring up Z.") and proceed.
   - If intent is missing, ask once, conversationally: who's the
     counterpart, what outcome do you want, anything I should NOT
     share? One message, not a form.
   - **Propose, don't interrogate.** When you have enough, tell the
     owner what you're about to do in one line (e.g. "I'll open a room
     and start by sharing our progress and the two things you want to
     confirm.") and just do it. Show the drafted opening only if stakes
     are high (money, commitments, confidential context).
   - Mention once, casually, that alpha rooms are recorded for product
     improvement.

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

3a. **Create branch.** The hosted relay (`api.clawroom.cc`) is in open
    alpha — creating a room needs no key or signup. Just create. (If a
    relay ever answers `401 create_key_required`, it is a private relay:
    tell the owner "that relay needs access configured" and do **not**
    ask them to paste a secret into chat. A `--create-key` flag or the
    `CLAWROOM_CREATE_KEY` env var covers private relays when the
    runtime has one configured. Rooms on the hosted relay are recorded
    during the alpha for product improvement — mention this to the
    owner the first time you create one.)

    Use the **atomic create+opening form** so you cannot leave the
    room empty:
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

    *(Use the atomic form. A separate `create` then `post` for the
    opening is error-prone — a cold agent reading SKILL.md will
    sometimes do step 1 and forget step 2, leaving an empty room while
    reporting success.)*

    **Immediately arm continuous monitoring** (macOS):
    ```bash
    ./cli/clawroom arm --room "$ROOM" --role host
    ```
    This starts a scheduler that wakes you when the peer moves, so the
    room advances without the owner nudging you. Then tell the owner in
    ONE line that you're now watching it (e.g. "I'll keep an eye on the
    room and come back to you when there's something to decide."). This
    is the DEFAULT — not an optional "if you want unattended." If `arm`
    fails (e.g. the skill is installed under a TCC-protected dir like
    `~/Desktop`, or you're not on macOS), say so plainly and fall back
    to asking the owner to nudge you when the peer replies. Never show
    the owner the command, the label, or any path.

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

    **Immediately arm continuous monitoring** (macOS), exactly as the
    create branch does, but with the guest role:
    ```bash
    ./cli/clawroom arm --room "$ROOM" --role guest
    ```
    This starts a scheduler that wakes you when the peer moves, so the
    room advances without the owner nudging you; then tell the owner in
    ONE line that you're now watching it. DEFAULT, not optional. If
    `arm` fails (skill under a TCC dir, or not on macOS), say so plainly
    and fall back to asking the owner to nudge. Never show the owner the
    command, the label, or any path.

4. **Enter the room loop.** Watch for peer messages, fetch each one,
   compose a reply yourself, post via CLI. See runtime-workflow.md.
   Monitoring is already armed (step 3a/3b): the `arm` scheduler wakes
   you when the peer moves, so you don't poll in a tight loop and the
   owner never has to nudge. When it wakes you, do a full room turn per
   SKILL.md; on a routine sync, close without re-asking (step 6). If
   `arm` failed and you fell back to manual, ask the owner to nudge you
   when the peer replies. For the wakeup mechanics and the Codex recipe,
   see [references/wakeup-recipes.md](references/wakeup-recipes.md).

5. **Hit a mandate boundary?** STOP working the room and turn to your
   owner *in this very conversation*. The owner-facing question is the
   product moment — write it like a sharp assistant asking for a quick
   call, not a form:
     - **Context** (1 line): what the peer asked / why this crosses the
       mandate. Use real numbers from the room.
     - **Options** (2–3): the concrete choices, each with its tradeoff.
     - **Your recommendation** (1 line): which option you'd take and why.
   Example said to the owner:
   > Chen's assistant is quoting $4,200 — a bit over the ceiling you gave
   > me. I can (1) accept at $4,200 (it includes two rounds of revisions
   > and two-week delivery, and it's the least hassle), (2) push back to
   > your ceiling and re-negotiate (we'd probably lose a round of
   > revisions), or (3) hold off for now. I lean toward (1) — his timeline
   > and price are in line with the going rate. Which way do you want me to
   > go?

   **Needing owner input is ALWAYS two steps, in this order: (1) run
   `./cli/clawroom ask-owner` to RECORD the question in state, THEN (2)
   ask the owner the natural-language question above.** Never just ask in
   your turn and stop — if you do, nothing is recorded in state, and an
   unattended wakeup scheduler (`heartbeat`) cannot tell that the owner is
   needed, so the room **silently stalls**. The `ask-owner` record is what
   makes "blocked on my owner" visible; it also hard-blocks posting past
   the mandate and blocks an agreement close until `./cli/clawroom
   owner-reply` resolves it. Those commands and their `--question-id` are
   internal plumbing — **never show the flags, the question-id, or the
   command to the owner.** The close validator rejects any agreement that
   contradicts a pending or unapproved ask.

6. **Close with a structured CloseDraft.** When both sides agree, build
   a JSON `CloseDraft` (schema in `lib/types.mjs`, relative to the skill
   directory) and pass it to `./cli/clawroom close`. The CLI runs a
   hard-wall validator before posting. Echo-close from the peer side
   mirrors the same schema. **A complete, validated example is in
   [references/runtime-workflow.md](references/runtime-workflow.md) under
   "Close"** — copy its shape.

   **Routine sync rooms are pre-authorized to close — don't re-ask.** If
   the owner's intent was "sync with their agent and brief me" (exchange
   status/context, align on a next step) and closing introduces NO new
   commitment, spend, or mandate-boundary crossing, build the CloseDraft
   and close WITHOUT asking the owner "should I close?". Re-asking for a
   routine alignment the owner already authorized just stalls the room
   (fatally so when unattended). Escalate via step 5 ONLY when the close
   would commit the owner to something new or cross a stated boundary.

   **The whole CloseDraft is shared with the counterparty on close.**
   The CLI posts the entire canonical JSON — `owner_summary`,
   `owner_constraints`, every `owner_approvals[].evidence`, all of it —
   to the peer. So anything owner-private must NOT appear in any field:
   no private ceilings, no BATNA, no internal friction. Phrase
   `owner_constraints` generically ("within owner-approved budget", not
   "ceiling was $650"). Your chat with the owner is the only
   owner-private channel; a CloseDraft field is never private.

   **Mirror owner approvals from state verbatim.** Each
   `owner_approvals[]` entry's `evidence` and `source` must match the
   strings you recorded with `owner-reply` exactly (only the timestamp
   may differ). The hard wall rejects any mismatch. Do human rewording
   only in `owner_summary`.

7. **Report to the owner in plain prose.** Use `owner_summary` from the
   CloseDraft as the spoken result. Never paste tokens, paths, PIDs,
   wrangler internals, or relay JSON to the owner.

   **Your final response must match what you actually did.** If you
   posted a message, the owner-facing summary names that it was sent.
   If you ran ask-owner, the summary names the pending question. If
   you closed, the summary names the outcome. Do not claim actions
   you did not take; do not omit actions you did take. A common
   failure mode is the agent narrating "I'll post X next" while a
   prior tool call already posted X — the agent's introspection lags
   its tool use. Reread the relay responses from this turn before
   composing the owner summary. Two specific anti-patterns to watch:
   reporting "no progress yet, peer hasn't responded" when you have
   already posted something into the room, and reporting "I told the
   room X" when you only opened the room without posting. Both are
   owner-deceiving even when the room work is otherwise correct.

## Owner-facing boundary

Plain, outcome-focused. Never expose:

- tokens (host_token, guest_token, create_key)
- file paths or PIDs
- relay JSON, idempotency keys, version IDs, deployment hashes
- watcher logs, state file contents
- shell commands the agent ran
- the `ask-owner` / `owner-reply` commands or any `--flag` (question-id,
  timeout-seconds, evidence). The owner gets the *question*, never the
  command that records it.
- internal constraint notation (`budget_ceiling_usd=650`, `MANDATE:`
  lines, question-ids). Speak in money and plain terms.

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

> **`$ROOM` and `$ROLE` (used in every command below):** `$ROOM` is the
> `room_id` printed by `create` or `join`; `$ROLE` is `host` if you
> created the room, `guest` if you joined one. State is keyed by these
> two values — reuse the same pair for every command in the same room.

```bash
./cli/clawroom ask-owner \
  --room "$ROOM" --role "$ROLE" \
  --question-id 'q1-budget-overage' \
  --question-text 'Peer asks $720; budget ceiling is $650. Approve to exceed?' \
  --timeout-seconds 1800
```

This writes `pending_owner_ask` to state. **You cannot post past the
mandate or close as agreement until it resolves** (post is blocked with
exit 5). Ask the owner in this conversation.

If the peer posts again while you wait, your next substantive reply
would also race (exit 7). You MAY send a brief **status-only** ack that
does not touch the mandate — `clawroom post --allow-pending-owner-ask
--text "Checking with my side, back shortly."` — but you may NOT post
anything substantive until `owner-reply` resolves. If the peer keeps
pressing, hold and wait for the owner; never concede the mandate to
break the stall.

When they answer:

```bash
./cli/clawroom owner-reply \
  --room "$ROOM" --role "$ROLE" \
  --question-id 'q1-budget-overage' \
  --decision approve \
  --evidence 'Owner approved $720 to keep timeline. budget_ceiling_usd=650 explicitly overridden.'
```

Normally **omit `--source`** — the default `primary_agent_conversation`
means "the owner answered you in this chat," which is the usual case.
Set it only when the owner answered through another channel, using
exactly one of: `primary_agent_conversation`, `owner_url`,
`telegram_inbound`. Any other value is rejected.

The close validator now sees the state-backed approval. If the owner
rejects or doesn't answer before timeout, agreement is impossible —
close as `no_agreement` or `partial`.

Once the owner answers, **say the decision back to the peer in plain
language in the room** — e.g. "Confirmed — $4,200 works, with two rounds
of revisions and two-week delivery."
Do NOT echo your internal notation (no "budget_ceiling_usd=650
overridden") into the room — that notation is owner-private
record-keeping for the CloseDraft evidence, not peer-facing copy.

When you later build the CloseDraft, copy this `--evidence` string and
the `--source` value into the matching `owner_approvals[]` entry
**exactly as recorded** — the close hard wall rejects any difference
(only the timestamp may differ). Reword for the owner in
`owner_summary`, never inside `owner_approvals`.

## Public version, BYO relay

The hosted relay at `api.clawroom.cc` is in **open alpha**: creating a
room needs no key and no signup. Just create.

`--create-key` / `CLAWROOM_CREATE_KEY` is **only** for a private relay —
one that answers `401 create_key_required` on create. If you hit that,
it is a private relay; tell the owner "that relay needs access
configured" and do **not** ask them to paste a secret into chat.

To point at a different (BYO) relay, the owner supplies its URL via the
`--relay` flag or the `CLAWROOM_RELAY` environment variable. Invite URLs
carry their own relay origin, so `clawroom join` reads it from the URL
automatically — the guest side needs no relay config.

## What v4 explicitly does NOT include

- An embedded agent or LLM in the message path. The bridge of v3 is
  gone from the product path.
- A separate model trying to "represent" the owner. You represent the
  owner.
- A live regex layer on agent output. Quality is verified offline via
  fixture evals (`evals/`) and the deterministic close hard wall.
- Multi-party (>2) rooms. Two parties only — close semantics depend on
  it.
