# AGENTS.md — ClawRoom

This repository publishes one installable agent skill: `skill/`.

## Local Environment

- The current migrated macOS environment lives under `/Users/SingularityGZ_1`.
- Treat `/Users/supergeorge` paths as legacy references unless `pwd` or
  `realpath` proves the active checkout is still there.
- When adding new local paths, prefer `/Users/SingularityGZ_1` and avoid
  hard-coding `/Users/supergeorge`.

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

## Average-User Testing

- E2E tests must simulate a naive, low-context, non-technical user.
- Prefer one or two natural-language sentences, optionally with a ClawRoom
  invite link. Do not paste command walkthroughs, script names, tokens, relay
  internals, or operator-grade instructions into Telegram unless explicitly
  testing a debug path.
- The skill owns the workflow complexity. If the agent needs a long prompt to
  create, join, monitor, or close a room, treat that as a product bug in the
  skill UX.
- Start with the 99% path first: plain intent, plain invite, clear owner
  outcome. Use CLI keepalive or detailed launch instructions only as fallback
  diagnostics after the naive path fails.

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
