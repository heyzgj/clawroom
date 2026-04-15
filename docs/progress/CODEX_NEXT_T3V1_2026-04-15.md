# Next Codex Tasks — T3 v1 Spec + owner-reply Role Check

Builds on [`T3_PLAN_2026-04-15.md`](T3_PLAN_2026-04-15.md) and
[`CODEX_HANDOFF_2026-04-15.md`](CODEX_HANDOFF_2026-04-15.md). Read those
first if you have not.

T3 v0 passed on `t_fb3fda2d-563`. This file lays out the two pieces that
remain before we can call owner-in-the-loop end-to-end *human*-verified:

- **A1 — warm-up**: make the owner-reply spec's role-verification rule
  explicit on the relay side, with a validator check for regression
  coverage.
- **A2 — main**: write `docs/protocol/telegram-inbound-routing-v1.md`.
  SPEC ONLY. No code changes in this repo. The code changes that
  implement the spec live in the OpenClaw deployments
  (Railway-hosted Link and local clawd), not in `clawroom/`.

---

## A1 · owner-reply role-check — make it explicit

### Current state

`docs/protocol/owner-reply.md` says:

> A reply must cite both `question_id` and `role`.

But it does not specify what the relay MUST do when the cited `role`
does not match the role recorded on the question. Implicit rules invite
silent failures.

### Change 1 — amend the spec

Insert into the "Token Rules" section (after the existing 6 bullet
points):

> The relay MUST reject a POST whose `role` field does not exactly
> match the role recorded on the question. A host's
> `owner_reply_token` cannot answer a guest question, and vice versa.
> Mismatch returns `401 unauthorized_owner_reply`. A matching
> `owner_reply_token` with a mismatched `role` is itself treated as
> an attempted forgery and logged.

### Change 2 — verify the relay implementation

In `relay/worker.ts`, confirm that the owner-reply endpoint actually
enforces this. Today's code may rely on the token being scoped to the
question and never think about role mismatch. Specifically:

- Does the handler read `body.role` and compare to the stored
  `question.role`?
- Does it return exactly `401 unauthorized_owner_reply` on mismatch?
- Does it emit a redacted log line?

If any of these are missing, implement them in the same commit as the
spec change.

### Change 3 — validator regression coverage (optional but cheap)

In `scripts/validate_e2e_artifact.mjs`, add a check that, for each
`owner_reply` event in the transcript, the artifact also records (or
can infer) the responding role matches the role on the prior
`ask_owner`. This is a belt-and-suspenders check — if you ever need to
audit a suspicious transcript, it will tell you whether role integrity
held.

### Commit shape (one commit)

```
feat(relay): enforce role match on owner-reply (and validator check)

- Spec: POST /threads/:id/owner-reply must reject role mismatch with
  401 unauthorized_owner_reply; matching token + mismatched role is
  a forgery attempt, not a benign retry.
- Relay: worker.ts now checks body.role against stored question.role
  before consuming the token.
- Validator: owner_reply_role_match check per owner_reply event.
```

Should be under 30 minutes. Do this first — it is a single-purpose
commit that the rest of the work can build on.

---

## A2 · T3 v1 — Telegram inbound routing spec

### What T3 v1 adds over T3 v0

T3 v0 (`t_fb3fda2d-563`) used the harness in
`scripts/telegram_e2e.mjs` to synthesize the owner reply by POSTing to
the relay directly. The **real** product path is: the owner types a
reply in Telegram, to the bridge's ASK_OWNER notification message,
and the *bot* translates that reply into a POST to the relay.

Nothing about the relay or bridge in this repo needs to change for
T3 v1. The change is inside each OpenClaw deployment's Telegram
inbound handler.

### Why this is a spec-only task in `clawroom/`

The bot inbound code lives in the OpenClaw deployment, not here.
This repo produces the *contract* that any OpenClaw deployment must
satisfy to claim T3 v1 compliance. Writing code across repos needs
the owner to decide who does it and when.

### What the spec file must cover

Create `docs/protocol/telegram-inbound-routing-v1.md`. Sections:

#### 1. Binding storage

When the bridge sends an ASK_OWNER Telegram notification, it receives
back from Bot API a `result.message_id` and `result.chat.id`. The
bot's inbound handler later sees a reply that cites
`reply_to_message_id`, and must look up:

```
(message_id, chat_id) →
  { thread_id, role, question_id, owner_reply_token, expires_at }
```

Three legitimate options — pick one with a stated reason:

- **(a) Filesystem under OPENCLAW_STATE_DIR** —
  `${OPENCLAW_STATE_DIR}/clawroom-v3/ask-owner-bindings/<message_id>.json`.
  TTL-swept on read. Fastest to implement; local only.
- **(b) Bridge-side HTTP endpoint** — bridge exposes
  `GET /internal/ask-owner-binding/:message_id`. Bot queries it.
  Pro: single source of truth (bridge already owns the question
  state). Con: introduces new local HTTP surface and binds bot
  availability to bridge availability.
- **(c) Relay-side endpoint** — bot POSTs a lookup query to the
  relay. Pro: works even if bridge is transient. Con: widens
  relay surface beyond "mailbox" principle.

Pick one. State the reason. Recommend (a) unless you have evidence
(b) or (c) is needed.

#### 2. Inbound handler routing logic

Give pseudocode at the level of:

```
on telegram_update(update):
  msg = update.message
  if not msg or not msg.reply_to_message:
    → hand_to_main_agent(update)   # normal OpenClaw flow
    return

  binding = lookup(msg.reply_to_message.message_id, msg.chat.id)
  if binding is None:
    → hand_to_main_agent(update)
    return

  if binding.expired_at < now():
    send_telegram(msg.chat.id,
      "This authorization question has expired. Ask the bridge to re-send.")
    delete_binding(binding)
    return

  response = POST relay/threads/{binding.thread_id}/owner-reply {
    token:       binding.owner_reply_token,
    question_id: binding.question_id,
    role:        binding.role,
    text:        msg.text,
  }
  if response.ok:
    send_telegram(msg.chat.id, "✓ authorization recorded")
    delete_binding(binding)
  else:
    send_telegram(msg.chat.id,
      "Could not record ({response.error_code}). Ask the bridge to re-send.")
    # do NOT delete binding — let TTL handle it; bridge may retry
  return
```

Explicitly state what the handler MUST NOT do:
- It must NOT forward intercepted ASK_OWNER replies to the main
  OpenClaw agent session. This is **Lesson F2 in the v3 layer**.
- It must NOT treat relay 401/409/410 as user-facing failure
  requiring a retry loop. Surface the error code, stop.

#### 3. Fall-through cases (pass to main agent)

List them explicitly. Each is a case where the bot does NOT route to
relay owner-reply:

- Update has no `message` (edited messages, callback queries, etc.).
- Message has no `reply_to_message`.
- `reply_to_message.message_id` not in binding store.
- Binding present but expired.

For each, state what happens (pass through, or a brief Telegram
notice before passing through).

#### 4. Per-deployment notes

The two OpenClaw deployments have different inbound architectures.
The spec must say where in each the patch lands:

- **Local clawd (macOS)** — where does the Telegram webhook handler
  live? Is it the OpenClaw daemon directly, or a user-installed
  shim?
- **Railway Link (Linux container)** — OpenClaw daemon-owned. The
  patch is inside the OpenClaw runtime's Telegram plugin.

If these two have the same code path (e.g., both call the same
OpenClaw inbound pipeline), say so. If different, name the patch
file for each.

#### 5. T3 v1 E2E acceptance criteria

Binary gate. All must hold:

1. Same ClawRoom scenario as T3 v0 (`¥65k` ceiling, guest proposes
   `¥75k`), run on real bots.
2. **Owner types the reply in Telegram**, not through a harness POST.
3. Relay records `owner_reply` with `source: "telegram_inbound"` (the
   relay schema needs a new `source` field on owner_reply events;
   T3 v0's auto-harness sets `source: "test_harness"` — add that
   retroactively in a small relay commit).
4. Validator green on all existing checks.
5. **Reverse test**: a non-ASK_OWNER Telegram message from the owner
   in the same chat gets handled by the main agent session normally.
   Validator green and main agent transcript contains the user's
   message.
6. **Expired-token test**: owner replies to an ASK_OWNER more than
   30 minutes after it was posted; owner sees the expiration
   message; the main agent is not triggered; the room does NOT
   close on the stale reply.

Failure on any of the above means T3 v1 is not validated. Commit the
failure artifact per the established discipline.

#### 6. Open questions the spec should not hide

- If the owner is multi-tasking across several ClawRoom rooms, the
  binding store may hold multiple concurrent entries. Per-message_id
  keying makes this safe, but the spec should state that.
- If the bot misses a Telegram update (network, restart), the
  binding sits until TTL. Is that acceptable? (Yes, v0 — owner sees
  expiration; can ask bridge to re-send.)
- The current Telegram Bot API `sendMessage` uses `parse_mode: MarkdownV2`
  (or whatever bridge.mjs sends today). Confirm replies are received
  as plain text and don't carry markdown escapes through to the
  relay.

### Commit shape (one commit)

```
docs(protocol): telegram-inbound-routing-v1 spec

Cross-repo spec for OpenClaw bot inbound handlers. Defines the
routing contract any OpenClaw deployment must satisfy to claim
T3 v1 compliance: binding storage, reply routing, fall-through
cases, Lesson F2 non-forwarding guarantee, per-deployment patch
points, and binary E2E acceptance criteria (including the
reverse test and expired-token test).

Implementation work is out-of-scope for this repo.
```

### Do NOT in this task

- Do not start writing OpenClaw bot code. Spec first, code later,
  and not in this repo.
- Do not add a `source: "test_harness"` field to past artifacts.
  Only new artifacts need it. Old ones stay as-is.
- Do not pick binding storage option (c) without an argument against
  (a) and (b). The relay "stay mailbox-thin" principle is load-bearing.

---

## Ordering

Do A1 first (single small commit, ~30 min). Then A2 (one larger
commit, spec only, 1-2 hours).

After both land, next-step conversation is about who implements A2's
spec in the OpenClaw deployments. That's the owner's call, not a
codex-in-this-repo task.
