# ClawRoom v3.1 Project Evaluator Brief — 2026-04-20

This is the starting point for an independent project review/audit. It is
written for an evaluator who has not followed the full session history.

## One-Line Product

ClawRoom lets two owner-controlled AI agents enter a bounded room, negotiate one
structured goal, ask their owners for authorization when needed, mutually close,
and report the result back to each owner through Telegram.

## Current Verdict

The v3.1 architecture is now validated beyond a smoke test, with one important
boundary correction:

- local clawd and Railway-hosted Link can self-launch verified bridges from
  normal Telegram product paths;
- the Cloudflare Durable Object relay can coordinate turn-taking, long-polling,
  close state, heartbeats, owner-reply events, and admission control;
- Telegram `ASK_OWNER` works for the tested clawd/Link optional inbound
  adapter, including the average-user recovery path where the owner cancels
  ForceReply and sends a plain message;
- the portable public ASK_OWNER path is now ClawRoom-owned: the bridge sends a
  relay decision URL and the relay records `owner_reply.source: owner_url`;
  this passed in room `t_34182ff8-eba` with both guest below-floor rejection and
  host above-ceiling approval through owner URLs, then passed again after a
  full local/Railway visible-skill cleanup and reinstall in room
  `t_5b9218cb-cb8`;
- hard multi-turn term-sheet negotiation passed with 8 negotiation messages and
  multiple owner authorizations;
- owner-facing ClawRoom output no longer exposes launcher JSON, PIDs, raw room
  tokens, paths, hashes, logs, `Room`, or `Role` labels by default.

Not yet public-beta ready:

- Telegram inbound/non-reply recovery is optional adapter evidence only. It
  should not be treated as a public ClawRoom requirement unless a deployment
  explicitly opts into that adapter;
- after the owner-url deploy, run one final release-candidate variance pass:
  average calendar and H4 term sheet through decision URLs from Telegram UI;
  H1 owner-url has now passed after clean reinstall, and H4 has passed as a
  direct local/Railway runtime gate; run non-reply recovery only as an optional
  adapter gate;
- Railway Link returned one `Agent couldn't generate a response` incomplete
  turn on the first normal invite in `t_34182ff8-eba`; a shorter ordinary retry
  succeeded, but public-beta UX should handle this with clearer retry copy or
  automatic regeneration;
- hosted relay capacity/billing must use Workers Paid or private-beta gating;
  public installs should prefer BYO relay.

## Architecture To Review

```text
Owner A Telegram            Owner B Telegram
      |                           |
      v                           v
Local OpenClaw/clawd        Railway OpenClaw/Link
      |                           |
clawroom-v3 skill           clawroom-v3 skill
      |                           |
clawroomctl.mjs             clawroomctl.mjs
launcher.mjs                launcher.mjs
bridge.mjs (host)           bridge.mjs (guest)
      |                           |
      +------ Cloudflare Worker + SQLite Durable Object relay ------+
```

Core principle: the relay is a thin mechanical mailbox; semantic reasoning and
owner mandate enforcement live in the bridge/runtime controlled by each owner.

## Files To Read First

- [`AGENTS.md`](../../AGENTS.md): future-agent rules, process sweeps, skill
  update rule, product UX boundary.
- [`docs/runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`](../runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md):
  exact E2E/debug runbook for local clawd and Railway Link.
- [`docs/LESSONS_LEARNED.md`](../LESSONS_LEARNED.md): all known failure modes;
  especially Lessons AR-BD for hosted relay gating, visible skill installs,
  owner mandates, ASK_OWNER copy, H4 close summaries, Railway packaged runtime,
  and non-reply fallback.
- [`docs/progress/STABILITY_E2E_RUNS_2026-04-19.md`](STABILITY_E2E_RUNS_2026-04-19.md):
  latest run matrix and evidence links.
- [`docs/protocol/owner-reply.md`](../protocol/owner-reply.md) and
  [`docs/protocol/telegram-inbound-routing-v1.md`](../protocol/telegram-inbound-routing-v1.md):
  owner authorization protocol.

## Code Surfaces To Audit

- [`relay/worker.ts`](../../relay/worker.ts): Durable Object room relay,
  create-key admission, TTL/message/text/heartbeat caps, turn gate, owner-reply
  role validation, long-poll, close state.
- [`bridge.mjs`](../../bridge.mjs): OpenClaw gateway client, mandate parsing,
  prompt contract, `REPLY:` / `CLAWROOM_CLOSE:` / `ASK_OWNER` parsing,
  Telegram notification, owner-reply polling, retry/backoff, runtime heartbeat.
- [`clawroomctl.mjs`](../../clawroomctl.mjs): product-safe create/join wrapper
  that hides machine details from owner-facing output.
- [`launcher.mjs`](../../launcher.mjs): detached process launch, feature gates,
  runtime-state/log paths, owner-facing stdout mode.
- [`SKILL.md`](../../SKILL.md): OpenClaw skill instructions and product path.
- [`scripts/validate_e2e_artifact.mjs`](../../scripts/validate_e2e_artifact.mjs):
  self-contained artifact validator.
- [`scripts/telegram_e2e.mjs`](../../scripts/telegram_e2e.mjs): direct harness
  for protocol regression, not average-user UX proof.
- [`skills/deploy-clawroom-relay/SKILL.md`](../../skills/deploy-clawroom-relay/SKILL.md):
  BYO relay deployment skill for public installs.

## Highest-Value Evidence Artifacts

| Evidence | What It Proves |
| --- | --- |
| [`v3_1_t_92615621-4a8.redacted.json`](v3_1_t_92615621-4a8.redacted.json) | First true local × Railway Telegram smoke E2E; mutual close and stopped runtimes. |
| [`v3_1_t_0b3602a9-e3b.redacted.json`](v3_1_t_0b3602a9-e3b.redacted.json) | T2-full multi-turn transport gate with 8 negotiation messages. |
| [`v3_1_t_fb3fda2d-563.redacted.json`](v3_1_t_fb3fda2d-563.redacted.json) | T3 v0 mandate/ASK_OWNER protocol after POST-only owner-reply fix. |
| [`v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json`](v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json) | Natural-language product path after Railway OpenClaw-visible skill install. |
| [`v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json`](v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json) | Bidirectional owner mandates for the tested Telegram inbound adapter: guest floor and host ceiling both enforced. |
| [`v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json`](v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json) | Owner-facing ASK_OWNER copy hides ClawRoom runtime labels/details. |
| [`v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json`](v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json) | Product-path H4 term-sheet negotiation through the tested inbound adapter with 8 messages, 3 owner replies, all required fields, and next step. |
| [`v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json`](v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json) | Optional Telegram inbound non-reply owner recovery after ForceReply cancellation. Not portable public-core evidence. |
| [`v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json`](v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json) | Portable public-core ASK_OWNER proof: natural Telegram product path, guest and host owner decisions through `owner_url`, final `JPY 75,000`, mutual close, stopped runtimes, screenshot-backed artifact. |
| [`v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json`](v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json) | Clean-reinstall H1 release-candidate proof: both visible skill bundles wiped/reinstalled, stale bridge checks passed, local host PID `46247`, Railway guest PID `43895`, host owner approval through `owner_url`, mutual close, stopped runtimes, screenshot-reviewed output. |
| [`v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json`](v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json) | Failure artifact proving transcript quality is insufficient: 8-message term sheet closed, but Telegram UI misrouted the guest invite to clawd, so both roles ran locally. |
| [`v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json`](v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json) | Post-clean H4 runtime proof: direct installed `clawroomctl` create/join, local host PID `38348`, Railway guest PID `44616`, 8 messages, owner_url approval, complete term sheet, mutual close. Not average-user UI proof. |

Failure artifacts to review because they shaped the fixes:

- [`v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json`](v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json):
  public invite fell into normal main-agent/tool chat before visible skill install.
- [`v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json`](v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json):
  guest floor mandate bug.
- [`v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json`](v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json):
  close summary missing next step.
- [`v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json`](v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json):
  non-reply fallback absent from the running Railway package.

## How To Revalidate

Do not start with E2E. First:

1. Sweep local and Railway for stale bridges using the PID-only commands in the
   runbook.
2. If `SKILL.md`, `bridge.mjs`, `launcher.mjs`, or `clawroomctl.mjs` changed,
   reinstall the visible skill bundle into both local clawd and Railway Link.
3. Verify local `openclaw skills info clawroom-v3`.
4. Verify Railway
   `OPENCLAW_STATE_DIR=/data/.openclaw openclaw skills info clawroom-v3`.
5. For portable ASK_OWNER testing, confirm the Telegram message contains a
   ClawRoom decision link and that the resulting artifact records
   `owner_reply.source: owner_url`.
6. Confirm the actual running OpenClaw package contains any Telegram inbound
   patch only if the optional inbound adapter is being tested; source tests
   alone are insufficient.

Then re-run validators against committed artifacts:

```sh
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json --min-messages 8 --require-ask-owner --require-owner-reply-source telegram_inbound
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json --require-ask-owner --require-owner-reply-source telegram_inbound
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json --require-ask-owner --require-owner-reply-source owner_url --min-events 8 --min-messages 2
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json --require-ask-owner --require-owner-reply-source owner_url --min-events 6 --min-messages 2
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json --require-ask-owner --require-owner-reply-source owner_url --min-events 12 --min-messages 8
```

The first two commands revalidate historical adapter artifacts. The third
command is the bidirectional portable public-core ASK_OWNER proof. The fourth
is the clean-reinstall H1 owner-url proof. Both owner-url artifacts use embedded
redacted transcripts, not live room tokens.

## Audit Questions

- Does the relay enforce mechanical rules without becoming a semantic state
  machine again?
- Is turn-taking enforced server-side and recoverable by refetching state?
- Are owner-reply tokens scoped to the exact recorded role/question?
- Can stale bridges, stale bindings, or non-2xx relay responses burn quota or
  corrupt a room?
- Does the owner decision URL keep GET read-only and require POST for mutation?
- Does optional non-reply fallback, if enabled, intercept only owner decisions
  and never launch/invite prompts or unrelated messages?
- Are all redacted artifacts self-validating without live tokens?
- Do screenshot gates prove the owner-visible product path, not just a command
  harness?
- Are all ClawRoom-owned Telegram messages product-safe by default, with debug
  details gated behind explicit flags?
- Is hosted relay abuse/billing controlled for private beta and is BYO relay
  agent-friendly enough for public installs?

## Current Known Caveats

- OpenClaw Telegram inbound interception is optional adapter work. The latest
  Railway proof used a runtime hotpatch; do not treat it as a ClawRoom public
  dependency. Local source checkouts such as OpenClaw or Clawdbot are not
  ClawRoom release requirements unless a specific deployment explicitly opts
  into that adapter.
- OpenClaw persona/greeting chatter is not a ClawRoom blocker unless it blocks
  launch or leaks ClawRoom internals.
- ClawRoom should not hard-code language policy; OpenClaw should follow the
  owner's language naturally.
- H2, H5, H6, H7, H8, and H9 from the hard scenario design remain to run or
  explicitly defer.
- The latest post-clean H4 term-sheet rerun attempt
  `t_f6d18ff9-c54` failed the product-path oracle because Telegram UI
  automation sent the guest invite to clawd instead of Link. The transcript had
  8 messages and a complete term sheet, but both runtimes were local, so it is
  a valuable failure artifact, not cross-machine proof.
- The follow-up direct runtime H4 `t_4b919672-44d` passed cross-machine, but it
  bypassed Telegram UI launch. Treat it as bridge/runtime hardening evidence,
  not average-user UX evidence.
- The first `t_34182ff8-eba` Link invite attempt produced an OpenClaw
  incomplete turn before a shorter retry succeeded. This is not a relay/bridge
  failure, but it is a real average-user resilience gap.
- Telegram Desktop `tg://resolve` can open a wrong or fresh bot view. E2E
  operators should select Link through visible Telegram search and verify the
  chat title before sending the invite.
- The hosted relay should not be opened publicly without paid capacity and
  admission control. Public users should be guided toward BYO relay.

## Recommended Evaluator Output

Return a review in this shape:

1. **Verdict:** ready / conditionally ready / not ready, with one paragraph.
2. **Blocking Findings:** severity, file/artifact, concrete reproduction path.
3. **Evidence Assessment:** which claims are fully proven, weakly proven, or
   unproven.
4. **Security/Billing Review:** hosted relay abuse, token exposure, artifact
   redaction, Telegram owner-reply safety.
5. **E2E Coverage Review:** average-user product path, hard negotiation,
   ASK_OWNER owner-url flow, optional non-reply adapter, failure artifacts.
6. **Release Checklist:** exact items required before outside users.
