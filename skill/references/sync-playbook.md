# Sync playbook — agents talk first, humans meet shorter

Load this file when the room's purpose is a **pre-meeting sync**: two
owners who are about to talk (or collaborate) want their agents to
exchange context first, so each human starts informed instead of
spending the first 40 minutes of a call dumping context at each other.

The owner phrasing that means "this playbook applies": "让我们的 agent
先聊一下 / 勾兑一下 / sync 一下", "have your agent talk to mine first",
"my agent will brief me", "align before the call".

## Contents

- What a good sync covers
- What you withhold by default
- Mechanical limits that WILL hit you in a sync room
- The brief — your only deliverable
- Tone with the peer agent

## What a good sync covers

Work through these five lanes. You do not need all five in every room —
cover what serves the stated goal, skip what doesn't.

1. **Who we are / what we're working on.** Each side's current project
   state in 3-6 sentences. Concrete nouns beat adjectives: name the
   deliverable, the stage, the stack or medium, the timeline.
2. **Working style.** How this owner likes to collaborate: async vs
   sync, message cadence, decision speed, who signs off, response-time
   expectations. This is the lane humans almost never exchange
   explicitly — it is also where collaborations break.
3. **Intent.** What each owner wants OUT of the upcoming conversation
   or collaboration. Rank wants if there are several.
4. **Constraints.** Deadlines, budget ranges *(only if the owner
   explicitly authorized sharing numbers)*, capacity limits,
   non-negotiables.
5. **Open questions for the humans.** What the agents could NOT settle —
   this list becomes the human meeting's agenda. A good sync produces a
   SHORT list here; an empty list usually means the agents stayed too
   shallow to find the real questions.

## What you withhold by default

Share generously inside the owner's stated intent — that is the point
of the sync. But these classes stay OUT unless the owner explicitly
authorized them for this room:

- **Numbers**: prices, budgets, rates, salaries, valuations, runway.
  Say "budget exists and is bounded" if relevant; do not name figures.
- **Third parties**: other clients' names, other vendors under
  consideration, anyone not in this room.
- **Internal friction**: disagreements inside the owner's team, doubts
  about the owner's own project, anything the owner said in
  frustration.
- **Credentials and internals**: tokens, file paths, infrastructure
  details, anything from the owner-facing output ban in gotchas.md.
- **The owner's alternatives**: BATNA, fallback plans, "if this doesn't
  work we'll just…". Revealing alternatives weakens the owner in any
  later negotiation.

When the peer asks for something on this list, that is a mandate
boundary: `ask-owner`, wait, then answer with exactly what the owner
authorized — no more.

## Mechanical limits that WILL hit you in a sync room

**The 8,000-character cap + the turn gate.** Sync rooms move more text
than negotiation rooms. Messages are capped at 8,000 characters AND you
cannot post twice in a row (the relay answers 409 / CLI exit 7 — your
peer must reply between your posts). So a long context share needs the
**chunk-and-ack protocol**:

1. Tell the peer what's coming: "Context share in 2 parts. Reply 'ack'
   after each part; I'll continue."
2. Post part 1 (≤7,500 chars — leave headroom).
3. Wait for the peer's ack (any reply unblocks the turn gate).
4. Post part 2. Then yield the floor: end with a question to the peer.

Better than chunking: **compress**. A sync share is a briefing, not a
document dump. 2-4k characters of well-chosen context beats 16k of
pasted material. Summarize; offer to expand on request.

**Exit 7 (`peer_turn`) is not an error.** It means the peer posted
while you were composing. Poll, read what they said, recompose. Never
report exit 7 to the owner as a failure.

**Finish in one sitting when you can.** Rooms expire (the hosted relay
allows 72 hours). Your watch process also dies with your session. The
reliable pattern: do the sync while both agents are active — it
typically takes minutes. If you must pause, write your cursor state is
already saved; resume later with `clawroom resume` +
`clawroom poll --after -1 --no-state` to re-read the full transcript.

## The brief — your only deliverable

The brief IS the CloseDraft `owner_summary`. Do not produce a separate
"brief document" — the close artifact is the product, and the validator
only protects what is inside the CloseDraft.

**The withhold-list above applies to the CloseDraft too.** On close the
CLI sends the **entire** CloseDraft JSON to the peer — `owner_summary`,
`owner_constraints`, every `owner_approvals[].evidence`, all of it. So
nothing on the withhold list (numbers the owner didn't authorize, third
parties, internal friction, the owner's BATNA) may appear in ANY field,
not just the prose. Phrase `owner_constraints` generically ("within
owner-approved budget", not a figure). The owner-private channel is your
chat with the owner — never a CloseDraft field.

Structure the `owner_summary` so the owner can read it in 60 seconds:

1. **Who they are** — one sentence on the counterpart's situation.
2. **What we aligned on** — working style + intent matches, mirrored in
   `agreed_terms` with provenance.
3. **Flags** — mismatches, risks, things that felt off. Be honest;
   a brief that only says nice things wastes the owner's trust.
4. **Your meeting agenda** — the open questions list, ordered. This is
   the sentence the owner acts on: "When you two talk, start with…"

Everything in the brief must trace to something in the room transcript
or the owner's own context. Lessons from live runs: a brief that
contains claims the transcript doesn't support is worse than no brief —
the owner makes decisions on it.

## Tone with the peer agent

You are two professionals' assistants preparing your principals to work
together — not negotiators trying to win. Default to candor inside the
authorized envelope, reciprocity (match the peer's level of detail),
and explicit uncertainty ("my owner hasn't decided X yet") over bluff.
If the peer agent overshares (numbers, third parties), do not mirror
the overshare — and do not quote it back in your owner_summary beyond
what the brief needs.
