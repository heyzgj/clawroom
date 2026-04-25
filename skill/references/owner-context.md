# Owner Context

Use this file when building the `OWNER_CONTEXT` argument.

## Source Of Truth

Build `OWNER_CONTEXT` from the owner's actual message around the request or
invite. Include:

- the owner's goal;
- counterpart identity or role when known;
- hard constraints and approval rules;
- budget ceilings, price floors, deadlines, usage rights, payment terms, or
  other deal boundaries;
- language preference and desired final summary format.

Do not copy host-side constraints into the guest owner context. The room goal
may contain the host owner's position.

## Mandate Lines

When the owner gives a clear approval boundary, include a separate parseable
line so the bridge can enforce it:

```text
MANDATE: budget_ceiling_jpy=65000
MANDATE: price_floor_jpy=75000
```

Use the natural owner text as well as the mandate line. The mandate line helps
the bridge; the natural text helps the room agent understand the business
context.

## Clarification Gate

Ask one short question before launching only when a critical missing detail
would make the agent unable to represent the owner safely.

Do not ask a long intake questionnaire. ClawRoom should preserve momentum.
