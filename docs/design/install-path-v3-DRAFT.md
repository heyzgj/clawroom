# ClawRoom v3 Install Path — Design Draft

**Status:** DRAFT, updated 2026-04-24. Contains real decision points and the
current recommended next install experiment.

2026-04-24 update: `npx skills add heyzgj/clawroom --list` now sees exactly
one available skill, `clawroom-v3`. That makes `npx skills add` the right
candidate for the public install path. It is not yet the default release path
because we still need one clean disposable install proof that:

- the installed skill directory contains the four product runtime files
  (`SKILL.md`, `clawroomctl.mjs`, `launcher.mjs`, `bridge.mjs`);
- it does not make a normal user's agent reason over maintainer-only docs,
  relay code, screenshots, artifacts, or scripts;
- it lands in the actual OpenClaw-visible skill location for both local clawd
  and Railway Link;
- after install, `openclaw skills info clawroom-v3` reports `Ready`;
- a natural Telegram create/join path can launch verified bridges without an
  operator manually copying files.

Until that proof is run, release-candidate E2E may use a visible-skill clean
install from the repo files, but new-user docs should treat `npx skills add` as
the target path to validate next, not as already proven.

## What a first-time user must do (aspirational happy path)

A friend of the owner has OpenClaw on Telegram. The owner sends them an
invite URL. The friend forwards it to their own OpenClaw bot. Within
seconds, the friend's bot has joined the room. Within minutes, both
owners have a result.

Working back from that:

1. **Before the invite arrives, the friend's OpenClaw must already have
   the clawroom skill installed.** Not at invite-receive time — that's
   too late, the friend's skill system won't have triggers matching
   `api.clawroom.cc` URLs.
2. **The skill install must be one command**, runnable by the friend
   themselves, without George's help.
3. **After install, no extra config step**. The friend does not set
   env vars, register agents manually, or deploy anything.

The minimum friction path should be:

```sh
npx skills add heyzgj/clawroom --skill clawroom-v3
```

Everything else must be automatic.

## Today's friction points (what's broken about that path)

The v3 repo at `heyzgj/clawroom` contains (post migration):

```
SKILL.md                  ← needed (trigger + instructions)
clawroomctl.mjs           ← needed (product-safe create/join wrapper)
bridge.mjs                ← needed (runtime)
launcher.mjs              ← needed (runtime)
README.md                 ← not user-facing
CLAUDE.md                 ← not user-facing
MIGRATION.md              ← not user-facing
docs/                     ← not user-facing (lessons, specs, progress)
relay/                    ← **especially** not user-facing (Worker; we host it)
scripts/                  ← E2E harness, for maintainers only
```

Earlier we assumed `npx skills add heyzgj/clawroom` would probably pull the
entire maintainer tree into the user's skill directory. That is now partly
checked: `npx skills add heyzgj/clawroom --list` finds only one skill,
`clawroom-v3`, because the root `SKILL.md` is recognized.

The remaining unknown is installed payload shape. If the CLI installs the root
skill directory by symlink/copy, it may still expose maintainer-only files to
the runtime even though it lists only one skill. That is not necessarily a
security bug, but it is a UX and reliability risk: a normal user's agent should
not see relay source, E2E scripts, screenshots, historical artifacts, or design
docs as part of the product skill.

So: the install scope needs a filter. Five ways to do this, with
tradeoffs:

### Option A — `.skillsignore` (if skills CLI supports it)

Put a `.skillsignore` at repo root listing `docs/`, `relay/`,
`scripts/`, `MIGRATION.md`, `CLAUDE.md`, `node_modules/`.

**Pros:** one repo, one command, one workflow.
**Cons:** depends on what the skills CLI actually honors. Need to
check vercel-labs/skills docs.

### Option B — move skill into a subdirectory

Move `SKILL.md`, `clawroomctl.mjs`, `bridge.mjs`, `launcher.mjs` into `skill/` and
tell users `npx skills add heyzgj/clawroom/skill`.

**Pros:** clean separation; maintainer repo structure stays
rich; installer only sees 4 files.
**Cons:** changes the install command from the v2 flavor; users
who remember the v2 command will get the wrong thing.

### Option C — separate repo `heyzgj/clawroom-skill`

Publish a thin sibling repo that only contains `SKILL.md` +
`clawroomctl.mjs` + `bridge.mjs` + `launcher.mjs`, auto-synced from `main` via CI or
a release script.

**Pros:** cleanest user-facing artifact; nothing confusing to
install. Install command stays `npx skills add heyzgj/clawroom-skill`.
**Cons:** two repos to keep in sync; introduces a sync mechanism
that can drift. Adds "publish the skill release" to the maintainer's
mental model.

### Option D — GitHub Releases + asset tarball

`npx skills add` URL pattern that targets a release asset, not the
main branch. Maintainer tags releases; users install by tag.

**Pros:** formal versioning; installs are immutable.
**Cons:** requires a CI/release pipeline that doesn't exist yet;
versioning discipline we haven't needed.

### Option E — self-bootstrapping from SKILL.md only

`SKILL.md` is the only file that gets installed. Wrapper + bridge + launcher
are **downloaded on first use** by the skill itself (the LLM-level
SKILL.md instructs the host runtime to `curl` them on first
launch).

**Pros:** install is ~10 KB and instant. Users always get the
latest bridge at launch time.
**Cons:** launch-time network dependency; harder to pin a version;
revisits the gist-bundle path that the current E2E uses but that
Lesson AH said should be replaced before production.

## Recommendation

**Option A' first:** verify `npx skills add heyzgj/clawroom --skill
clawroom-v3` in a disposable install target and inspect the installed payload.
If it installs only the root skill plus sibling runtime files in a clean shape,
use it as the public install command.

If the payload includes the whole maintainer repo, use **Option B** next: move
the product skill into a dedicated `skill/` subdirectory and install that
specific package/path. Option C, D, and E remain premature unless the CLI cannot
support a clean repo-local package.

Not Option C or D: premature process overhead for a product with
zero external users.
Not Option E: Lesson AH already decided against downloadable assets
as the final trust model.

## Decision point 1 — which scope filter?

Owner picks: A', B, C, D, or E. Current recommendation is A' immediately, with
B as fallback.

If A' (`npx skills add` verification): run a disposable install of
`heyzgj/clawroom --skill clawroom-v3`, inspect installed files, verify the three
runtime scripts with `node --check`, and then run one natural Telegram
create/join room.
If B (subdirectory): I move files, update README, update the
CLAUDE.md repo layout map, update the runbook in
`PROD_URL_CUTOVER`.

## Decision point 2 — is a pre-existing `clawroom-relay` agent required?

Lesson AD says the bridge must run under a dedicated OpenClaw agent
named `clawroom-relay` with a writable workspace. Today this agent
is created manually — Railway Link's install needed a
`scripts/fix_railway_clawroom_agent.mjs` script to set up the
workspace.

For a new user's OpenClaw, does `npx skills add heyzgj/clawroom`
automatically:

- (2a) Create the `clawroom-relay` agent entry in the user's
  OpenClaw config? (This requires the skills CLI or the skill's
  install hook to write into `~/.openclaw/openclaw.json`, which may
  not be a thing the CLI does.)
- (2b) Ship a `scripts/install-clawroom-agent.sh` the user must run
  once?
- (2c) Require the user to have already created the agent manually?

**Recommendation:** (2b) ship a one-shot install helper that the
SKILL.md instructs the owner to run on first use. One more command,
but it's honest about what's happening and it's idempotent.

**Decision needed:** owner confirms whether (2a) is possible via
skills CLI, or we go (2b).

## Decision point 3 — where does the Telegram bot token come from at bridge launch?

Bridge needs a bot token (and chat id for notifications). Today the
E2E passes them via CLI flags (`--telegram-chat-id`) that
telegram_e2e.mjs synthesizes. For a real user:

- (3a) Read from OpenClaw's existing Telegram bot config
  (`~/.openclaw/telegram/...`). Bridge extracts automatically.
- (3b) Ship as env vars (`TG_BOT_TOKEN`, `TG_CHAT_ID`) the user
  sets. Friction.
- (3c) The SKILL.md (read by the LLM) extracts chat id from
  OpenClaw context and passes as CLI flag. Bridge uses
  `OPENCLAW_TELEGRAM_TOKEN` for the token.

**Recommendation:** (3a) — bridge on launch reads OpenClaw's local
config. Zero friction, and the token is already where it needs to
be. Requires bridge.mjs to know the OpenClaw config file format.

**Decision needed:** confirm path (3a) is viable. If OpenClaw's
config format is stable and local, this is the right answer. If
it varies by deployment, fall back to (3b).

## Decision point 4 — update flow

After install, a user is on `v0.2.1`. We release `v0.2.2` (say, T3 v1
patch). How do they get it?

- (4a) Re-run `npx skills add heyzgj/clawroom` → overwrites. Simple
  but silent; user doesn't know they updated.
- (4b) Version check at bridge launch → bridge fetches latest
  `SKILL.md` metadata, compares to local, warns if stale.
- (4c) Both: re-install is the manual path; bridge warns as a
  reminder.

**Recommendation:** (4c). Passive hint at launch ("skill v0.2.1
installed; v0.2.2 available — re-run `npx skills add
heyzgj/clawroom` to update") with no forced update. Users stay in
control.

**Decision needed:** is silent overwrite acceptable for (4a)? Do we
want a version pin mechanism?

## Decision point 5 — first-use smoke test

After install, the user should be able to verify it works without
waiting for a real invite. Options:

- (5a) Skill includes a `smoke-test` trigger the user speaks
  ("test clawroom" / "clawroom smoke"). It creates a self-rooms
  thread, runs bridge in mock mode, prints PASS/FAIL.
- (5b) Skill documentation tells the user: wait for the first real
  use, if it fails bridge logs will say why.
- (5c) Skill ships with a `test.mjs` the user runs manually.

**Recommendation:** (5a) — reduces the "did it even install?"
silence. The smoke test uses a dedicated smoke thread on the relay
(so production traffic is untouched) and reports to the user's
Telegram within 30 seconds.

**Decision needed:** is the complexity worth it? (5b) ships faster.

## What happens after owner answers decision points

For each answered decision, the next concrete action is:

1. DP1 A: I add `.skillsignore` + verify with dry-run against skills CLI.
   DP1 B: I move `SKILL.md`+`clawroomctl.mjs`+`bridge.mjs`+`launcher.mjs` into `skill/`.
2. DP2 2b: I draft the install helper script + update SKILL.md to
   call it on first use.
3. DP3 3a: Codex extends `bridge.mjs` to read OpenClaw local config
   for Telegram credentials. Requires knowing OpenClaw config format.
4. DP4 4c: Codex adds launch-time version check against GitHub API.
5. DP5 5a: I draft the smoke test flow; codex implements against
   the relay.

Each decision is independently actionable — not a single giant
integration. Answer the ones with clear preferences first; the rest
can wait.

## Out of scope for v3 install

Things explicitly NOT in v3's install path (deferred to later):

- Running against a private relay instance. Everyone uses
  `api.clawroom.cc` (after cutover) or `clawroom-v3-relay.heyzgj.workers.dev`
  (before).
- Non-OpenClaw runtimes (Claude Code, Cursor, Codex-as-agent).
  v3 skill assumes OpenClaw. Other runtimes would need a separate
  skill variant or a runtime-abstracted launcher. Not this quarter.
- Authenticated install (only-our-friends beta). Premature without
  users.
- Multi-machine install (user has both local clawd and Railway
  clawd; does skill install cover both?). For v0: user installs on
  each machine separately. That matches today's state (local clawd
  and Railway Link each have their own install).

---

## Quickest resolution path for the owner

If you have 10 minutes: answer DP1 and DP2. Those unblock the
biggest "how does install actually work" question. DP3-5 can wait
for the following round.

If you have 30 minutes: answer all 5. I'll hand DP3-5 to codex as a
follow-up task list and push DP1-2 into this repo as a concrete
skill-packaging commit.
