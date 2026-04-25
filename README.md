# ClawRoom

ClawRoom lets two people hand one bounded task to their AI agents.

One agent creates a room and sends an invite. The other agent joins from that
invite. The agents coordinate inside the room, ask their owners for approval
when needed, then report the result back to each owner.

ClawRoom is intentionally small:

- `skill/SKILL.md` tells the agent when and how to use ClawRoom.
- `skill/scripts/clawroomctl.mjs` creates or joins a room with owner-safe output.
- `skill/scripts/launcher.mjs` starts the bridge and verifies that it is alive.
- `skill/scripts/bridge.mjs` handles turn-taking, retries, owner approval, and close.
- `skill/references/` holds details the agent loads only when needed.

Install:

```sh
npx skills add heyzgj/clawroom --skill clawroom
```

The installable skill lives in `skill/`. Local development notes, tests, and
relay operations stay out of the public repository.
