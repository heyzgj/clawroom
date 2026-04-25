# AGENTS.md — ClawRoom

This repository publishes one installable agent skill: `skill/`.

Before ClawRoom work, check for leftover bridge processes:

```sh
pgrep -f '^node .*bridge\.mjs --thread' || true
```

Kill only stale processes you understand, then verify the list is empty.

## Repository Shape

- The public skill package lives in `skill/`.
- Keep the installable package small: `SKILL.md`, `scripts/`, and
  `references/`.
- Do not add docs, progress notes, screenshots, E2E artifacts, relay operation
  guides, or maintainer-only experiments to the installed skill package.
- Do not add additional installable skills to this repo unless the owner asks
  for a multi-skill repository.

## Skill Rules

- `skill/SKILL.md` must be English-only and product-facing.
- Do not expose raw JSON, commands, tokens, PIDs, paths, hashes, logs, create
  keys, or relay internals in owner-facing copy.
- Keep the owner path plain: create a room, share the public invite, ask for
  approval through the ClawRoom decision page when needed, then summarize.
- Use human language and progressive disclosure. Technical detail belongs
  behind explicit debug requests.

## Verification

When changing files in `skill/`, run:

```sh
node --check skill/scripts/clawroomctl.mjs
node --check skill/scripts/launcher.mjs
node --check skill/scripts/bridge.mjs
npx skills add . --list
```

For install-shape changes, also install into a temporary directory and confirm
that only the `skill/` package is copied.
