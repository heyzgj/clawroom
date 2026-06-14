# ClawRoom cross-owner E2E harness

A deterministic, fully-recorded, zero-manual-intervention test of the
real product flow: two independently-owned AI agents, each cold, each
installing the skill from scratch, coordinating one task to mutual
close — driven only by **IQ-50 owner-voice prompts** with no technical
input from the operator.

This exists because hand-driven rehearsals kept contaminating the
evidence (stale installs shadowing fresh ones, the "guest" not being a
real cold process, operator nudges leaking connection mechanics). The
harness makes the four guarantees mechanical instead of relying on
operator discipline.

## The four guarantees, and how each is enforced

| Owner requirement | Enforcement |
|---|---|
| 1. No stale clawroom anywhere | `scrub.sh` deletes every skill-install + state location, then **verifies clean and exits non-zero if anything survives**. The run aborts if scrub fails. |
| 2. Zero clawroom memory at session start | Turn 1 each side is a **cold process** (`codex exec` / `claude -p`, no resume). The only clawroom knowledge an agent has is the skill it installs itself + the owner's pasted block. Later turns resume only that agent's own session (a real assistant remembers the conversation), never any test/clawroom priming. |
| 3. IQ-50 owner voice, no technical terms | Prompts come verbatim from `scenarios/*.txt`, written as a normal non-technical person speaks. The ONLY technical content is the ship block the friend literally pastes (the product itself). The harness adds zero connection mechanics; between-turn nudges are plain owner voice. |
| 4. Everything recorded | Every turn's stdout/stderr → `runs/<scenario>-<ts>/{host,guest}/turnN.log`; room state snapshotted via the operator export API at every transition → `runs/.../snapshots/*.json`; `index.log` is the timeline. Nothing is ephemeral. |

## Run it

```bash
# default: both sides cold codex (gpt-5.5 high) — works today
bash evals/e2e/run-e2e.sh 01-sync

# cross-vendor once claude CLI is logged in:
GUEST_DRIVER=claude bash evals/e2e/run-e2e.sh 01-sync

# the escalation scenario (forces ask-owner / owner-reply E2E):
bash evals/e2e/run-e2e.sh 02-escalation
```

Drivers: `codex` (gpt-5.5, high reasoning) | `claude` (opus, xhigh).
`claude` needs a logged-in CLI; if it 401s, the turn is recorded as a
failure and the run stops clean (not a silent hang).

Requires `docs/operator-admin-key.local.txt` (the relay admin key) for
the room-state snapshots — that's how the harness sees whose turn it is
without ever touching a token.

## The simulated human relay

The one thing a human does that the harness must stand in for: copying
the invite the host's agent produced and pasting it to the other
person. The harness extracts the invite URL the host agent **surfaced
to its owner** (exactly what a human copies) and splices it into the
guest scenario at `__FORWARD_FROM_HOST__`. It never hands the guest a
token or tells it how to connect — the guest agent fetches the invite
and figures out join itself, like a real cold agent would.

## Scenarios

- `01-sync` — professional pre-meeting sync (travel mini-program
  collaborators). No mandate boundary; tests the happy path + brief
  quality + boundary discipline (channel names withheld).
- `02-escalation` — owner hires a designer; host owner sets a private
  price ceiling and says "ask me before agreeing if they go over."
  Forces the `ask-owner → owner-reply` path end-to-end. Note: the
  harness's automated nudges can't *answer* an owner question — when a
  run hits a real `pending_owner_ask`, that's the point where a human
  (you) would answer. Inspect the snapshot and continue the host turn
  with the owner's decision as the prompt.

## Reading a run

`runs/<scenario>-<ts>/`:
- `index.log` — the timeline (scrub result, each FIRE, room state at
  every step, final close state).
- `host/turnN.log`, `guest/turnN.log` — full agent output per turn.
- `snapshots/*.json` — operator exports (redacted: no tokens) at every
  transition; the last one (`99-final.json`) is the full transcript.

## Known fidelity limits (state them in any writeup)

- Same machine, one user account, ONE copied Codex auth token shared by
  both sides. So **cross-*role* is real** (host vs guest tokens, custody
  fence) but **cross-*owner* identity is simulated** — there is no second
  account/principal. Room state is role-keyed (`~/.clawroom-v4`,
  host/guest never collide) and each agent's work dir + `CODEX_HOME` are
  isolated outside the repo, but a true two-different-humans /
  two-accounts / two-devices / two-networks test is the real alpha, not
  this harness.
- `codex×codex` by default — cross-vendor (`codex×claude`) needs the
  claude CLI logged in.
- The escalation scenario's owner-answer step is the one place a human
  is genuinely in the loop (by design — that IS the product's
  owner-approval moment).
