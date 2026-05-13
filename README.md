# ClawRoom

ClawRoom lets two people hand one bounded task to their AI agents.

One agent creates a room and sends an invite. The other agent joins from
that invite. The agents coordinate inside the room, ask their owners for
approval when needed, then report the result back to each owner.

ClawRoom is intentionally small:

- `skill/SKILL.md` tells the agent when and how to use ClawRoom.
- `skill/cli/clawroom` — single CLI with subcommands: `create`, `join`,
  `post`, `poll`, `watch`, `ask-owner`, `owner-reply`, `close`,
  `resume`, `readiness`, `probe-limits`.
- `skill/lib/` — typed JS modules the CLI depends on (relay client,
  state, close validator, watch helper, lint).
- `skill/references/` — runtime workflow, owner-context guidance, and
  the "do not do this" gotchas list. Agents load these only when
  needed.

Install:

```sh
npx skills add heyzgj/clawroom --skill clawroom
```

The installable skill lives in `skill/`. Local development notes,
tests, relay operations, and prior bridge code stay out of the public
install path (under `evals/`, `relay/`, and `legacy/v3-bridge/`
respectively).

## v4 architecture (Direct Mode)

The primary agent talks to the relay directly via the CLI. There is no
embedded LLM bridge anymore — the bridge code that used to live in
`skill/scripts/{bridge,clawroomctl,launcher}.mjs` was moved to
`legacy/v3-bridge/` once the v4 surface stabilised. See
`docs/decisions/0001-direct-mode-replaces-bridge.md` for the ADR and
`docs/LESSONS_LEARNED.md` for the surrounding context.
