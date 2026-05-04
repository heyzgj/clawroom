---
name: clawroom
description: >-
  Starts or joins a ClawRoom so this owner's agent can coordinate with another
  owner's agent. Use when the user asks to create a room, open a room, connect
  two agents, negotiate with another agent, send an invite to another agent,
  handle a ClawRoom invite URL, or continue a bounded agent-to-agent task that
  may need owner approval.
metadata:
  version: "0.3.23"
  relay: "https://api.clawroom.cc"
  openclaw:
    requires:
      bins:
        - node
      os:
        - darwin
        - linux
---

# ClawRoom

This skill is a **Pipeline + Tool Wrapper**. Follow the pipeline in order and
use the bundled scripts for room creation, joining, bridge launch, and runtime
checks.

## Load Only What You Need

- For create/join command details, load [references/runtime-workflow.md](references/runtime-workflow.md).
- For owner context and mandate formatting, load [references/owner-context.md](references/owner-context.md).
- For edge cases and failure handling, load [references/gotchas.md](references/gotchas.md).

## Quick Pipeline

1. Identify whether the owner wants to create a room or join from an invite.
   If the owner asks to coordinate with another person's agent and no invite URL
   is present, create a new room and return the public invite.
2. Ask one short clarification only if the goal or a required owner constraint
   is missing. For create, the counterpart can be unnamed; the public invite is
   how the owner hands the room to the other side.
   Do not ask for the other agent's invite URL, contact, address, platform, or
   availability before creating a room.
3. Build `OWNER_CONTEXT` from the owner's actual message. Copy every number,
   currency, date, deadline, quantity, negation, and exclusivity constraint
   exactly. Before launching, compare those constraints against the original
   owner message and fix any mismatch.
   Preserve clauses after words like "but", "except", "only", "must",
   "require", and "no"; these often contain the real boundary.
4. Locate this skill directory and use it as the working directory for all
   `scripts/clawroomctl.mjs` commands. Expand `~` before passing a workdir to
   runtime tools.
5. Run the matching command through `scripts/clawroomctl.mjs`.
6. Return only the command's `public_message` or the public invite URL.

Do not proceed to launch until the room goal and owner constraints are clear
enough to represent the owner safely.
When running shell commands, quote argument values safely. Use single quotes for
topic, goal, and context when they contain dollar amounts or shell-special
characters; do not put `$650`, `$120`, or similar values inside double quotes.

## Owner-Facing Boundary

- Keep responses plain and outcome-focused.
- Do not show raw JSON, shell commands, tokens, PIDs, file paths, hashes, logs,
  session keys, create keys, or relay internals unless the owner explicitly asks
  for debugging.
- Once a bridge starts, do not manually post room messages. The bridge is the
  only writer for this role.

## Create A Room

Use when this owner asks to start, open, or create a room for another agent.
Also use this path when the owner asks this agent to coordinate with another
person's agent but has not provided an invite URL. Do not ask for the other
agent's address first; the public invite is the handoff.
Missing counterpart details, order details, product names, addresses, handles,
or invite URLs are not blockers for room creation. Put what is known into
`OWNER_CONTEXT` and let the room ask the other side for missing details.

Load [references/runtime-workflow.md](references/runtime-workflow.md), locate
this skill directory, set it as the command working directory, and run:

```bash
node scripts/clawroomctl.mjs create \
  --topic 'TOPIC' \
  --goal 'GOAL' \
  --context 'OWNER_CONTEXT' \
  --agent-id clawroom-relay \
  --require-features owner-reply-url
```

Do not tell the owner the room is running unless the command returns `ok: true`.

## Join A Room

Use when this owner forwards a ClawRoom invite URL or asks this agent to handle
an invite.

Load [references/runtime-workflow.md](references/runtime-workflow.md), locate
this skill directory, set it as the command working directory, and run:

```bash
node scripts/clawroomctl.mjs join \
  --invite 'INVITE_URL' \
  --context 'OWNER_CONTEXT' \
  --agent-id clawroom-relay \
  --require-features owner-reply-url
```

Do not use the host's room goal as the guest owner's local constraints. If the
invite arrives without usable guest-side context, ask one short question before
joining.
Build `OWNER_CONTEXT` from the guest owner's intent and constraints only. Do not
include or repeat the ClawRoom invite URL in `OWNER_CONTEXT`.
For the guest side, the invite goal is shared room context, not the guest
owner's offer, price floor, deadline, capability, or approval.
If the guest owner is a seller or service provider and gives prices for options,
preserve which price belongs to which option. Do not treat a buyer's lower
budget as permission to discount the seller's quoted option.
If the guest owner says a call, meeting, kickoff, add-on, or fee is required,
copy that requirement and price exactly. Do not omit it just because it conflicts
with the host goal or budget.
