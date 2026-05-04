# Owner Context

Use this file when building the `OWNER_CONTEXT` argument.

## Source Of Truth

Build `OWNER_CONTEXT` from the owner's actual message around the request or
invite. Include:

- the owner's goal;
- counterpart identity or role when known, but do not require one before
  creating an invite room;
- hard constraints and approval rules;
- budget ceilings, price floors, deadlines, usage rights, payment terms, or
  other deal boundaries;
- language preference and desired final summary format.

Do not copy host-side constraints into the guest owner context. The room goal
may contain the host owner's position.
When joining as guest, treat the invite goal as shared context only. Guest
pricing, delivery capability, approvals, and floors must come from the guest
owner's message.
For seller or service-provider owners, preserve item-specific prices. A quoted
price for one option is not permission to accept a lower buyer budget for that
same option. If the owner says a price is a floor, minimum, lowest acceptable
amount, or non-negotiable quote, also add the matching `MANDATE:` line.

Copy owner constraints exactly. Do not shorten, round, normalize, translate, or
reinterpret numbers, currencies, dates, deadlines, quantities, negations, or
exclusivity terms. Before launching, compare the command's topic, goal, and
`OWNER_CONTEXT` against the owner's original request; fix any mismatch first.
Pay special attention to clauses after "but", "except", "only", "must",
"require", and "no". These are often the owner's real boundary. If a seller says
they require a paid call, meeting, kickoff, add-on, or fee, copy both the
requirement and the price exactly even when it conflicts with the buyer's goal.

## Mandate Lines

When the owner gives a clear approval boundary, include a separate parseable
line so the bridge can enforce it:

```text
MANDATE: budget_ceiling_usd=650
MANDATE: price_floor_usd=900
MANDATE: budget_ceiling_jpy=65000
MANDATE: price_floor_jpy=75000
```

Use the natural owner text as well as the mandate line. The mandate line helps
the bridge; the natural text helps the room agent understand the business
context.

## Clarification Gate

Ask one short question before launching only when a critical missing detail
would make the agent unable to represent the owner safely.

Do not block room creation only because the counterpart identity, invite URL,
contact method, product name, order detail, delivery address, or handle is
missing. Include known facts and note missing facts as items to ask inside the
room.

Do not ask a long intake questionnaire. ClawRoom should preserve momentum.
