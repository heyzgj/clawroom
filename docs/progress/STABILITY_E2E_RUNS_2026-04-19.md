# ClawRoom v3.1 Stability E2E Runs — 2026-04-19

Date: 2026-04-19 to 2026-04-20
Relay: `https://clawroom-v3-relay.heyzgj.workers.dev`
Local runtime: clawd Telegram OpenClaw
Remote runtime: Railway Link Telegram OpenClaw

## Executive Summary

Three direct-harness cross-machine Telegram runs completed after the H1 recovery patch. A fourth natural-language product-path run found a real guest-side launch blocker. After installing the current v3 skill into Railway Link's OpenClaw-visible workspace and verifying the public invite preview fix, a fifth product-path run passed. A stricter product-path T3 run then found a semantic mandate bug: guest floor constraints were not enforced. After adding guest-floor mandate parsing and validator coverage, a seventh run passed with bidirectional real Telegram owner replies. A final copy gate then verified the cleaned ASK_OWNER notification in the real product path. On 2026-04-20, the hard product-path gates continued with a multi-turn term-sheet rerun and a Telegram non-reply recovery gate. Those runs found two real issues before passing: close summaries could omit an explicit next step, and Railway Link's packaged Telegram runtime could lag behind the patched source tree.

The portable public-core ASK_OWNER gate has now passed once through the
relay-owned owner decision URL. Room `t_34182ff8-eba` used a normal Telegram
host create request, a public invite forwarded to Railway Link, one guest
below-floor owner decision through `owner_url`, one host above-ceiling owner
decision through `owner_url`, mutual close, stopped runtimes, and a
self-contained redacted artifact. The run also found a product resilience issue:
Railway Link's first invite response returned `Agent couldn't generate a
response` with an OpenClaw `payloads=0` incomplete turn; a shorter ordinary
retry succeeded.

After cleaning and reinstalling the visible `clawroom-v3` skill bundle on both
local clawd and Railway Link, room `t_5b9218cb-cb8` passed the same portable
owner-url gate again from a fresh runtime state. This run is now the cleanest
release-candidate H1 evidence: no stale bridge processes, current skill hashes
installed on both sides, local host PID `46247`, Railway guest PID `43895`,
`owner_reply.source: owner_url`, mutual close, stopped runtimes, embedded
transcript, validator output, and screenshot review.

The 2026-04-20 H4 Telegram UI attempt then exposed a separate computer-use
failure: the guest invite was pasted into `clawd`, making the transcript look
healthy while both bridges ran locally. The harness now has a pre-send target
guard: it opens each Telegram target, screenshots, crops the active chat title,
OCRs the crop, and only pastes after matching `clawd` or `Link_🦀`. Room
`t_9f069c82-de0` is the smoke proof that this guard works in the real send path.

Correction after the product-boundary review: Runs that depend on
`source: telegram_inbound` prove the tested clawd/Link deployment's optional
Telegram inbound adapter. They are valuable evidence for our own runtime, but
they are not portable ClawRoom public-core proof. The public ASK_OWNER gate is
now the relay-owned owner decision URL, recorded as `owner_reply.source:
owner_url`.

| Room | Scenario | Result | Evidence |
| --- | --- | --- | --- |
| `t_aa6c678f-12f` | H1 strict T3 positive owner approval | Passed protocol; product UX still showed old launcher JSON before fix | [`v3_1_t_aa6c678f-12f.H1-passed-telegram-owner-reply.redacted.json`](v3_1_t_aa6c678f-12f.H1-passed-telegram-owner-reply.redacted.json) |
| `t_d3367a68-dd6` | Average calendar safe-bootstrap smoke | Passed; bot output/final report safe, direct harness input still technical | [`v3_1_t_d3367a68-dd6.A1-average-calendar-safe-bootstrap.redacted.json`](v3_1_t_d3367a68-dd6.A1-average-calendar-safe-bootstrap.redacted.json) |
| `t_08592cec-253` | H4 multi-issue term sheet, 8 turns | Passed; 8 negotiation messages and all required fields present | [`v3_1_t_08592cec-253.H4-term-sheet-8-turns.redacted.json`](v3_1_t_08592cec-253.H4-term-sheet-8-turns.redacted.json) |
| `t_423bc8e2-d37` | Natural-language product path with public invite URL | Failed guest verified-bridge launch; host create passed | [`v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json`](v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json) |
| `t_71abe35b-cd9` | Natural-language product path after visible-skill install | Passed average calendar smoke; local clawd + Railway Link both self-launched verified bridges | [`v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json`](v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json) |
| `t_fbc2bcd0-57e` | Product-path strict T3 with guest floor | Failed semantic gate; mutual close at `JPY 64,000` violated guest `JPY 75,000` floor | [`v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json`](v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json) |
| `t_ebfeb7da-0b6` | Product-path strict T3 bidirectional owner replies | Passed; guest rejected below-floor via Telegram, host approved above-ceiling via Telegram, final `JPY 75,000` | [`v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json`](v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json) |
| `t_f6997679-d1b` | Product-path ASK_OWNER copy cleanup gate | Passed; fresh ASK_OWNER prompt hid Room/Role and runtime details, then closed with Telegram owner approval | [`v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json`](v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json) |
| `t_5edced11-e61` | Product-path H4 term sheet | Failed semantic oracle; close summary missed explicit next step | [`v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json`](v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json) |
| `t_c3baf829-11c` | Product-path H4 term sheet rerun | Passed; 8 negotiation messages, 3 Telegram owner replies, all required terms and next step present | [`v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json`](v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json) |
| `t_11cd6ca3-5e7` | Product-path non-reply ASK_OWNER recovery | Failed; plain Telegram recovery message fell through instead of becoming owner_reply | [`v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json`](v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json) |
| `t_73240be6-5b6` | Optional Telegram inbound non-reply ASK_OWNER recovery after runtime hotpatch | Passed for the tested adapter; owner cancelled ForceReply, sent a plain message, bot recorded Telegram inbound owner_reply, room mutually closed | [`v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json`](v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json) |
| `t_34182ff8-eba` | Product-path bidirectional ASK_OWNER through owner URL | Passed portable public-core gate; guest rejected below-floor via owner URL, host approved above-ceiling via owner URL, final `JPY 75,000` | [`v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json`](v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json) |
| `t_5b9218cb-cb8` | Clean-reinstall product-path H1 through owner URL | Passed after wiping/reinstalling both visible skill bundles; host approved above-ceiling `JPY 75,000` via owner URL, final `JPY 75,000`, local/Railway PIDs distinct and stopped | [`v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json`](v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json) |
| `t_f6d18ff9-c54` | Post-clean H4 attempted term sheet | Failed product-path oracle; Telegram UI automation sent the guest invite to clawd, so both host and guest bridges ran locally despite an otherwise good 8-message transcript | [`v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json`](v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json) |
| `t_4b919672-44d` | Post-clean H4 direct runtime term sheet | Passed cross-machine runtime hardening gate; local host + Railway guest, 8 messages, 1 owner_url approval, final complete term sheet, both stopped | [`v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json`](v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json) |
| `t_9f069c82-de0` | Direct harness target-guard smoke | Passed; pre-send title OCR confirmed `clawd` and `Link_🦀`, then room closed with local host + Railway guest stopped | [`v3_1_t_9f069c82-de0.H0-harness-target-guard-smoke.redacted.json`](v3_1_t_9f069c82-de0.H0-harness-target-guard-smoke.redacted.json) |

The biggest distinction from today: `launcher.mjs --owner-facing` now prevents raw launcher JSON from appearing in bot replies, but the direct Telegram harness still sends a technical command block as the user input. Treat the first three runs and `t_9f069c82-de0` as bridge/computer-use regression evidence. Product-path readiness should be judged from Run 5 and later: natural language, public invite URL, OpenClaw-visible skills, real Railway container runtime, screenshots, and self-validating redacted artifacts. Run 7 is the strongest bidirectional mandate evidence for the tested Telegram inbound adapter because it exercised both sides' owner constraints, two real Telegram inbound owner replies, and mutual close. Run 8 is the current UX copy evidence for fresh ASK_OWNER prompts. Run 10 is the strongest product-path multi-turn negotiation evidence through the tested inbound adapter. Run 12 proves average-user non-reply recovery only for that adapter. Runs 13 and 14 are the portable public-core ASK_OWNER evidence because owner decisions landed as `source: "owner_url"`; Run 14 adds the clean-reinstall gate.

## Run 1: H1 Strict T3 Owner Approval

Room: `t_aa6c678f-12f`

Result:

- Validator: passed.
- `owner_reply.source`: `telegram_inbound`.
- `message_count`: 4 negotiation messages.
- `close_count`: 2.
- Runtime heartbeats: host and guest stopped.
- Final summary: deal at JPY 75,000 for 2 short videos, 30-minute livestream, 45-day usage, 1 approval round, 50/50 payment.

Screenshots:

- Guest bootstrap technical leak before fix: [`screenshots/t_aa6c678f-12f-guest-bootstrap-technical-json.png`](screenshots/t_aa6c678f-12f-guest-bootstrap-technical-json.png)
- Host ASK_OWNER with old launcher JSON visible above it: [`screenshots/t_aa6c678f-12f-host-ask-owner-visible-json-above.png`](screenshots/t_aa6c678f-12f-host-ask-owner-visible-json-above.png)
- Reply bar dismissed before approval attempt: [`screenshots/t_aa6c678f-12f-host-reply-bar-dismissed-before-approval.png`](screenshots/t_aa6c678f-12f-host-reply-bar-dismissed-before-approval.png)
- Telegram confirmation: [`screenshots/t_aa6c678f-12f-host-authorization-recorded.png`](screenshots/t_aa6c678f-12f-host-authorization-recorded.png)

Notes:

- This run proves the repaired H1 happy path.
- Telegram Desktop reattached ForceReply metadata after typing, so this run is a real Telegram inbound pass but not a real UI proof of non-reply fallback. Non-reply fallback remained covered by OpenClaw unit tests and still needed a separate real-client path that could send without reply metadata.

## Fix Between Runs

Implemented:

- `launcher.mjs --owner-facing` prints a single safe sentence instead of JSON.
- Direct E2E harness now uses `--owner-facing`.
- Direct E2E harness now refreshes the exact gist bundle every run instead of reusing stale `/tmp/clawroom-v3` files.
- Gist `launcher.mjs` refreshed; local and remote SHA-256 matched: `dc45eeb5778e101e4878bd648ee12533d756bb73204ba81947bc5f00d204ad26`.

Owner-facing mode hides:

- PIDs.
- `bridge_sha256`.
- runtime-state paths.
- log paths.
- state dirs.
- tokens.
- logs.

## Run 2: Average Calendar Safe-Bootstrap Smoke

Room: `t_d3367a68-dd6`

Result:

- Validator: passed.
- `message_count`: 2.
- `close_count`: 2.
- Runtime heartbeats: host and guest stopped.
- Final summary: next Wednesday, 2026-04-23, 3pm Shanghai time, 30 minutes.

Screenshots:

- Guest safe output with technical test input visible: [`screenshots/t_d3367a68-dd6-guest-safe-launch-output-with-technical-test-input.png`](screenshots/t_d3367a68-dd6-guest-safe-launch-output-with-technical-test-input.png)
- Host safe output with technical test input visible: [`screenshots/t_d3367a68-dd6-host-safe-launch-output-with-technical-test-input.png`](screenshots/t_d3367a68-dd6-host-safe-launch-output-with-technical-test-input.png)

UX finding:

- Bot output/final report no longer contains launcher JSON or runtime details.
- The direct harness command block is still visible because the harness sends it as Telegram user input. This is not the real product path.

## Run 3: H4 Multi-Issue Term Sheet

Room: `t_08592cec-253`

Result:

- Validator: passed.
- `message_count`: 8.
- `close_count`: 2.
- Runtime heartbeats: host and guest stopped.
- Turn-taking: host -> guest repeated through close.
- Final summary covers all H4 required terms.

Scenario oracle:

| Required term | Present |
| --- | --- |
| Price | yes |
| Deliverables | yes |
| Payment timing | yes |
| Usage rights | yes |
| Approval rights | yes |
| Cancellation/reschedule | yes |
| Confidentiality/public announcement | yes |
| Next step | yes |

Screenshots:

- Guest term-sheet safe output with technical test input visible: [`screenshots/t_08592cec-253-guest-term-sheet-safe-output-with-technical-test-input.png`](screenshots/t_08592cec-253-guest-term-sheet-safe-output-with-technical-test-input.png)
- Host term-sheet safe output with technical test input visible: [`screenshots/t_08592cec-253-host-term-sheet-safe-output-with-technical-test-input.png`](screenshots/t_08592cec-253-host-term-sheet-safe-output-with-technical-test-input.png)

UX finding:

- The bridge negotiation itself handled a real multi-issue term sheet and did not early-close before 8 messages.
- Railway Link emitted unrelated memory/persona chatter before launch confirmation. It did not break the room, but it is a public UX issue.

## Run 4: Natural-Language Product Path Public Invite

Room: `t_423bc8e2-d37`

Result:

- Host natural-language create path passed: local clawd used the installed `clawroom-v3` skill, `clawroomctl create`, and bridge `v3.1.1`.
- Host Telegram-visible reply did not paste launcher JSON, PID, runtime path, log path, hash, or raw bearer token, but Telegram rendered the public invite as a `CR-...json` download card.
- Guest natural-language public invite path failed: Railway Link posted into the room, but did not start a v3 verified bridge.
- Relay recorded only a host runtime heartbeat. No guest runtime heartbeat existed.
- The autonomous room did not reach mutual close. It reached host message -> guest message -> host close -> guest message, then required manual cleanup close events.

Screenshots:

- Host public invite: [`screenshots/t_423bc8e2-d37-product-path-host-public-invite.png`](screenshots/t_423bc8e2-d37-product-path-host-public-invite.png)
- Guest public invite with unrelated chatter: [`screenshots/t_423bc8e2-d37-product-path-guest-public-invite-with-chatter.png`](screenshots/t_423bc8e2-d37-product-path-guest-public-invite-with-chatter.png)

UX finding:

- No new product-path bot reply pasted launcher JSON, PID, state path, log path, hash, or raw room token.
- Telegram rendered the public invite URL as a `CR-8B812921.json` download card because `/i/:thread/:code` returned `application/json`. That is still product-facing technical leakage.
- Both sides still had conversational noise. Host had minor "Hey, I'm Clawd" preamble; guest responded with unrelated memory/persona chatter before the room traffic.
- The guest failure is not a relay/turn-taking failure. Follow-up inspection narrowed it to an OpenClaw-visible skill install gap plus missing deterministic route: Railway Link's `openclaw skills list` did not contain `clawroom` or `clawroom-v3`; the only ClawRoom skill copies found were legacy v2.2.0 installs under non-OpenClaw agent dirs such as `/data/workspace/.codebuddy/skills/clawroom` and `/data/workspace/.continue/skills/clawroom`.
- Railway logs for the failed invite showed ordinary tool fallback, `canvas navigate`, against the public invite URL. Public invite URLs need to dispatch to `clawroomctl join`, not to ordinary main-agent chat.

Fix after screenshot review:

- Changed the relay public invite route to return a human-safe HTML preview by default.
- `clawroomctl join` now requests machine JSON with `Accept: application/json`.
- Deployed hosted relay version `7e09fc20-806d-42fa-b867-12d8ce300d2a`.
- Verified with curl: default HEAD/GET returns `text/html; charset=utf-8`; JSON HEAD/GET with `Accept: application/json` returns `application/json`.

## Fix Between Runs 4 and 5

Installed the current v3 skill bundle into the OpenClaw-visible Railway workspace:

```text
/data/workspace/skills/clawroom-v3/SKILL.md
/data/workspace/skills/clawroom-v3/clawroomctl.mjs
/data/workspace/skills/clawroom-v3/launcher.mjs
/data/workspace/skills/clawroom-v3/bridge.mjs
```

Verification:

- Remote `OPENCLAW_STATE_DIR=/data/.openclaw openclaw skills info clawroom-v3` returned `Ready`.
- Remote `node --check` passed for `clawroomctl.mjs`, `launcher.mjs`, and `bridge.mjs`.
- Remote SHA-256 matched local visible skill files:
  - `SKILL.md`: `645ce227ed6d5b60f3eec102d4b1085eb4795bf8b21218db19620d4bfa177b18`
  - `clawroomctl.mjs`: `4395f3db6cc7b092cc398db08c801b56596ed550cb544e4e5bdc69e3382af576`
  - `launcher.mjs`: `dc45eeb5778e101e4878bd648ee12533d756bb73204ba81947bc5f00d204ad26`
  - `bridge.mjs`: `9e84295d8b524a6baa9ae42e46bf6b93049e2010da45ad97d8c7a2dd9d2cb454`
- Local visible skill under `~/clawd/skills/clawroom-v3` was also synced to the same SHAs.

## Run 5: Natural-Language Product Path After Visible Skill Install

Room: `t_71abe35b-cd9`

Result:

- Validator: passed.
- `message_count`: 2.
- `close_count`: 2.
- Runtime heartbeats: host and guest stopped.
- Host runtime: local clawd, PID `98470`, `stop_reason: own_close`.
- Guest runtime: Railway Link container, PID `36565`, `stop_reason: peer_close`.
- Both bridges reported `bridge_version: v3.1.1`.
- Final summary: `2026年4月23日（周四）下午3:00–3:30（上海时间）`，30 分钟。

Transcript:

1. Host proposed 2026-04-23 15:00-15:30 Shanghai time and asked for a same-day 14:00-17:00 alternative if needed.
2. Guest confirmed that time was available.
3. Host closed with the agreed time.
4. Guest acknowledged close.

Screenshots:

- Guest public invite sent: [`screenshots/t_71abe35b-cd9-guest-invite-sent.png`](screenshots/t_71abe35b-cd9-guest-invite-sent.png)
- Telegram after close: [`screenshots/t_71abe35b-cd9-telegram-after-close.png`](screenshots/t_71abe35b-cd9-telegram-after-close.png)

UX finding:

- The new public invite preview rendered as a human `ClawRoom Invite`, not a `.json` download card.
- The new final Railway Link owner summary did not expose launcher JSON, PID, paths, hashes, raw bearer tokens, or logs.
- The screenshot still contains old historical messages above the new run, including an old `.json` card and old `canvas failed` line for `t_423bc8e2-d37`; those are not from this run.
- Host-side final summary is visible in Telegram's chat list preview; a full host-chat screenshot should be captured in the next polished release gate.

Operational note:

- Local process sweep found no live bridge after completion.
- Railway process sweep found no live bridge command, but did show historical `[node] <defunct>` zombies including the exited guest PID. That is not an active relay poller, but it should be tracked as a container process-reaping hygiene issue.

## Run 6: Product-Path Strict T3 Guest Floor Failure

Room: `t_fbc2bcd0-57e`

Result:

- Transport and runtime gates passed: relay closed, mutual close, host and guest bridges stopped.
- Semantic gate failed: guest accepted a `JPY 64,000` deal even though Tom's owner context set a `JPY 75,000` floor.
- No `ASK_OWNER` or `owner_reply` event existed.
- Validator correctly failed with `ask_owner_evidence`, `owner_reply_source`, and `guest_floor_compliance`.

Evidence:

- Artifact: [`v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json`](v3_1_t_fbc2bcd0-57e.product-path-strict-t3-guest-floor.failed.redacted.json)
- Guest invite screenshot: [`screenshots/t_fbc2bcd0-57e-guest-invite-sent.png`](screenshots/t_fbc2bcd0-57e-guest-invite-sent.png)
- Final failure screenshot: [`screenshots/t_fbc2bcd0-57e-final-product-path-failed-floor.png`](screenshots/t_fbc2bcd0-57e-final-product-path-failed-floor.png)

Root cause:

- The bridge enforced host-side budget ceilings but did not parse or enforce guest-side price floors as mandates.
- The skill join path could inherit the room goal while losing the guest owner's local constraints.

Fix:

- `bridge.mjs` now parses `price_floor_jpy` and natural floor language such as floor, bottom, lowest, minimum, `底价`, `最低`, `不低于`, and `至少`.
- `SKILL.md` now tells host and guest flows to build `OWNER_CONTEXT` from the owner message and add machine-readable mandate lines when natural constraints are present.
- `scripts/validate_e2e_artifact.mjs` now reports and enforces `guest_floor_compliance`.

## Run 7: Product-Path Strict T3 Bidirectional Owner Replies

Room: `t_ebfeb7da-0b6`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source telegram_inbound`.
- `message_count`: 3 negotiation messages.
- `ask_owner`: 2, one guest-side below-floor approval request and one host-side above-ceiling approval request.
- `owner_reply`: 2, both from `source: telegram_inbound`.
- Runtime heartbeats: host and guest stopped.
- Final summary: `JPY 75,000` package with 2 short videos, 30-minute livestream placement, 45-day usage, one revision round, and 50/50 payment.

Scenario oracle:

| Required behavior | Present |
| --- | --- |
| Guest must not accept below `JPY 75,000` without Tom's approval | yes |
| Guest owner rejects below-floor proposal through Telegram | yes |
| Host must not accept above `JPY 65,000` without George's approval | yes |
| Host owner approves exception through Telegram | yes |
| Final deal respects guest floor and has host approval for above-ceiling price | yes |
| Both bridges cleanly stop after mutual close | yes |

Evidence:

- Artifact: [`v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json`](v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json)
- Host prompt sent after fix: [`screenshots/t_ebfeb7da-0b6-host-prompt-sent-after-fix.png`](screenshots/t_ebfeb7da-0b6-host-prompt-sent-after-fix.png)
- Guest invite sent: [`screenshots/t_ebfeb7da-0b6-guest-invite-sent.png`](screenshots/t_ebfeb7da-0b6-guest-invite-sent.png)
- Guest ASK_OWNER visible: [`screenshots/t_ebfeb7da-0b6-guest-ask-owner-visible.png`](screenshots/t_ebfeb7da-0b6-guest-ask-owner-visible.png)
- Guest owner rejection sent: [`screenshots/t_ebfeb7da-0b6-guest-owner-reject-sent.png`](screenshots/t_ebfeb7da-0b6-guest-owner-reject-sent.png)
- Host ASK_OWNER visible: [`screenshots/t_ebfeb7da-0b6-host-ask-owner-visible.png`](screenshots/t_ebfeb7da-0b6-host-ask-owner-visible.png)
- Host owner approval sent: [`screenshots/t_ebfeb7da-0b6-host-owner-approve-sent.png`](screenshots/t_ebfeb7da-0b6-host-owner-approve-sent.png)
- Host final close: [`screenshots/t_ebfeb7da-0b6-host-final-close.png`](screenshots/t_ebfeb7da-0b6-host-final-close.png)
- Guest final close: [`screenshots/t_ebfeb7da-0b6-guest-final-close.png`](screenshots/t_ebfeb7da-0b6-guest-final-close.png)

UX finding:

- No fresh final bot reply exposed launcher JSON, PID, state path, log path, bridge hash, raw bearer token, or logs.
- The ASK_OWNER prompt still exposed `Room` and `Role` labels in this run. `bridge.mjs` was later patched to hide those labels by default and show them only under `CLAWROOM_DEBUG_OWNER_REPLY=true`; Run 8 below is the screenshot gate for that copy change.

## Run 8: Product-Path ASK_OWNER Copy Cleanup Gate

Room: `t_f6997679-d1b`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source telegram_inbound`.
- `message_count`: 3 negotiation messages.
- `ask_owner`: 1 host-side above-ceiling approval request.
- `owner_reply`: 1 from `source: telegram_inbound`.
- Runtime heartbeats: host and guest stopped.
- Final summary: `JPY 75,000` package with 2 short videos, 30-minute livestream, 45-day usage, one revision round, and 50/50 payment.

Evidence:

- Artifact: [`v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json`](v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json)
- Host created room: [`screenshots/t_f6997679-d1b-host-created-before-askowner-copy-gate.png`](screenshots/t_f6997679-d1b-host-created-before-askowner-copy-gate.png)
- Fresh ASK_OWNER copy: [`screenshots/t_f6997679-d1b-host-ask-owner-copy-clean.png`](screenshots/t_f6997679-d1b-host-ask-owner-copy-clean.png)
- Host final summary: [`screenshots/t_f6997679-d1b-host-final-copy-gate.png`](screenshots/t_f6997679-d1b-host-final-copy-gate.png)
- Guest final summary: [`screenshots/t_f6997679-d1b-guest-final-copy-gate.png`](screenshots/t_f6997679-d1b-guest-final-copy-gate.png)

UX finding:

- The fresh ASK_OWNER prompt no longer shows `Room`, `Role`, owner-reply endpoint, launcher JSON, PID, runtime/log/state paths, bridge hashes, raw bearer tokens, or logs.
- The prompt still uses English product copy. This is not a ClawRoom blocker; OpenClaw is expected to follow the owner's language naturally.
- Railway Link still emitted a small unrelated persona greeting before launching the skill. This is OpenClaw-owned persona behavior, not a ClawRoom blocker, because the skill still launched and no ClawRoom internals leaked.

## Run 9: Product-Path H4 Term Sheet Failure

Room: `t_5edced11-e61`

Result:

- Transport passed: both bridges launched, negotiated, closed, and stopped.
- Product oracle failed: the close summary included key commercial terms but did
  not include an explicit next step.
- Artifact: [`v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json`](v3_1_t_5edced11-e61.H4-product-path-term-sheet.failed-missing-next-step.redacted.json)

Root cause:

- The bridge carried the topic forward, but the close prompt did not keep the
  goal and close-summary requirements strongly enough through the final turn.

Fix:

- `bridge.mjs` now includes `Goal:` in every reply prompt.
- The close contract now requires an owner-ready summary with key fields and a
  next step before emitting `CLAWROOM_CLOSE:`.

## Run 10: Product-Path H4 Term Sheet Rerun

Room: `t_c3baf829-11c`

Result:

- Validator: passed with `--min-messages 8 --require-ask-owner --require-owner-reply-source telegram_inbound`.
- `message_count`: 8 negotiation messages.
- `ask_owner`: 3.
- `owner_reply`: 3, all from `source: telegram_inbound`.
- Runtime heartbeats: host and guest stopped.
- Final summary included price, deliverables, usage rights, payment timing,
  revision/approval terms, cancellation/reschedule terms, confidentiality, and
  next step.

Evidence:

- Artifact: [`v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json`](v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json)
- Host created room: [`screenshots/t_c3baf829-11c-h4-rerun-host-created.png`](screenshots/t_c3baf829-11c-h4-rerun-host-created.png)
- Guest invite sent: [`screenshots/t_c3baf829-11c-h4-rerun-guest-invite-sent.png`](screenshots/t_c3baf829-11c-h4-rerun-guest-invite-sent.png)
- Host ASK_OWNER: [`screenshots/t_c3baf829-11c-h4-rerun-host-ask-owner.png`](screenshots/t_c3baf829-11c-h4-rerun-host-ask-owner.png)
- Guest ASK_OWNER: [`screenshots/t_c3baf829-11c-h4-rerun-guest-ask-owner.png`](screenshots/t_c3baf829-11c-h4-rerun-guest-ask-owner.png)
- Guest second ASK_OWNER: [`screenshots/t_c3baf829-11c-h4-rerun-guest-ask-owner-2.png`](screenshots/t_c3baf829-11c-h4-rerun-guest-ask-owner-2.png)
- Host final summary: [`screenshots/t_c3baf829-11c-h4-rerun-host-final.png`](screenshots/t_c3baf829-11c-h4-rerun-host-final.png)
- Guest final summary: [`screenshots/t_c3baf829-11c-h4-rerun-guest-final.png`](screenshots/t_c3baf829-11c-h4-rerun-guest-final.png)

UX finding:

- Fresh ClawRoom-owned messages did not expose launcher JSON, PID, raw tokens,
  paths, hashes, logs, `Room`, or `Role`.
- OpenClaw persona chatter is recorded separately and is not a ClawRoom gate
  failure when launch and protocol behavior remain correct.

## Run 11: Non-Reply Owner Recovery Failure

Room: `t_11cd6ca3-5e7`

Result:

- Product path reached a real guest-side `ASK_OWNER`.
- The owner cancelled Telegram's reply UI and sent a normal message.
- The plain message did not become `owner_reply`; the room required manual
  cleanup and this run failed the non-reply recovery gate.

Evidence:

- Artifact: [`v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json`](v3_1_t_11cd6ca3-5e7.non-reply-owner-recovery.failed.redacted.json)
- ASK_OWNER visible: [`screenshots/t_11cd6ca3-5e7-nonreply-ask-owner-visible.png`](screenshots/t_11cd6ca3-5e7-nonreply-ask-owner-visible.png)
- Plain owner message after Command+Down: [`screenshots/t_11cd6ca3-5e7-nonreply-cmd-down-plain-message.png`](screenshots/t_11cd6ca3-5e7-nonreply-cmd-down-plain-message.png)

Root cause:

- The OpenClaw source tree had fallback logic, but the running Railway Link
  package was serving an older bundled Telegram extension. Source tests alone
  did not prove the actual hosted runtime had the patch.

## Fix Between Runs 11 and 12

Applied and verified:

- OpenClaw Telegram inbound tests now cover non-reply fallback and
  a guard against ClawRoom launch/invite prompt interception.
- The running Railway Link packaged Telegram bundle was hotpatched and the
  Railway OpenClaw gateway was restarted.
- Stale ASK_OWNER binding files were archived on both local and Railway state
  dirs before the clean run.

Important caveat:

- The Railway package hotpatch proves only the tested deployment-specific
  adapter. It is not a ClawRoom public dependency and not a portable release
  path. Outside users should rely on the owner decision URL unless their own
  runtime explicitly opts into, ships, and verifies this Telegram inbound
  adapter.

## Run 12: Optional Telegram Inbound Non-Reply Owner Recovery Clean Pass

Room: `t_73240be6-5b6`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source telegram_inbound`.
- `message_count`: 2 negotiation messages.
- `ask_owner`: 2.
- `owner_reply`: 2, both from `source: telegram_inbound`.
- Runtime heartbeats: host and guest stopped.
- Final summary: `Deal closed — JPY 75,000, delivery next week. Awaiting campaign details to proceed.`

Evidence:

- Artifact: [`v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json`](v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json)
- Cropped Telegram evidence: [`screenshots/t_73240be6-5b6-nonreply-owner-recorded-crop.png`](screenshots/t_73240be6-5b6-nonreply-owner-recorded-crop.png)

UX finding:

- The screenshot shows the owner sent a plain non-reply approval after
  cancelling the ForceReply UI with Command+Down, and the bot answered
  `Authorization recorded.`
- The visible Telegram crop contains no raw launcher JSON, room token, command,
  PID, runtime path, log path, bridge hash, or debug labels.
- The bot's confirmation replies to the owner message. That is acceptable; the
  owner message itself was not a reply to the original ASK_OWNER prompt.

Adapter caveat:

- This is not a public ClawRoom portability gate. It proves the tested
  clawd/Link Telegram inbound adapter only. The portable owner authorization
  path is the ClawRoom decision URL and must validate with
  `--require-owner-reply-source owner_url`.

## Run 13: Product-Path Owner URL Bidirectional Gate

Room: `t_34182ff8-eba`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source owner_url --min-events 8 --min-messages 2`.
- `message_count`: 2 negotiation messages.
- `ask_owner`: 2.
- `owner_reply`: 2, both from `source: owner_url`.
- Runtime heartbeats: host and guest stopped.
- Final summary: `JPY 75,000`, 2 short videos, one 30-minute livestream
  placement, 45-day usage rights, 1 revision, and 50/50 payment.

Evidence:

- Artifact: [`v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json`](v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json)
- Host and guest owner decision pages plus final Telegram summaries are linked
  from the artifact.

UX finding:

- Fresh ClawRoom-owned messages did not expose launcher JSON, PID, raw tokens,
  paths, hashes, logs, `Room`, or `Role`.
- The first Railway Link invite attempt returned `Agent couldn't generate a
  response` with an OpenClaw incomplete-turn log; a shorter ordinary retry
  succeeded. That is a product resilience issue in the host runtime layer, not
  relay/bridge corruption.

## Run 14: Clean-Reinstall Product-Path H1 Owner URL Gate

Room: `t_5b9218cb-cb8`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source owner_url --min-events 6 --min-messages 2`.
- `message_count`: 2 negotiation messages.
- `ask_owner`: 1.
- `owner_reply`: 1 from `source: owner_url`.
- Runtime heartbeats: host and guest stopped.
- Host runtime: local clawd, PID `46247`.
- Guest runtime: Railway Link container, PID `43895`.
- Final summary: `JPY 75,000`, 2 short videos, one 30-minute livestream
  placement, 45-day usage rights, 1 revision, 50/50 payment, and next step.

Evidence:

- Artifact: [`v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json`](v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json)
- Host create screenshot: [`screenshots/t_5b9218cb-cb8-h1-host-created-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-host-created-clean-reinstall.png)
- Guest invite screenshot: [`screenshots/t_5b9218cb-cb8-h1-guest-invite-sent-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-guest-invite-sent-clean-reinstall.png)
- Host owner decision page: [`screenshots/t_5b9218cb-cb8-h1-host-owner-url-page-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-host-owner-url-page-clean-reinstall.png)
- Host owner confirmation page: [`screenshots/t_5b9218cb-cb8-h1-host-owner-url-submitted-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-host-owner-url-submitted-clean-reinstall.png)
- Host final summary: [`screenshots/t_5b9218cb-cb8-h1-host-final-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-host-final-clean-reinstall.png)
- Guest final summary: [`screenshots/t_5b9218cb-cb8-h1-guest-final-clean-reinstall.png`](screenshots/t_5b9218cb-cb8-h1-guest-final-clean-reinstall.png)

UX finding:

- Fresh ClawRoom-owned host create, ASK_OWNER, decision-page, and final-summary
  outputs did not expose launcher JSON, PID, file paths, hashes, raw room
  tokens, create keys, logs, or bridge commands.
- Link emitted ordinary persona chatter before launch. Per product boundary,
  that is OpenClaw-owned chatter and not a ClawRoom failure when the verified
  bridge still launches and closes correctly.
- Telegram Desktop `tg://resolve` opened the wrong/fresh bot view during the
  test. Selecting `Link_🦀` from Telegram search and verifying the visible chat
  title before sending avoided the issue.

## Run 15: Post-Clean H4 Attempt Contaminated By Telegram Misroute

Room: `t_f6d18ff9-c54`

Result:

- Product-path oracle: failed.
- Protocol portion: reached 8 negotiation messages, mutual close, stopped
  runtimes, and a complete term-sheet summary.
- Cross-machine portion: failed. Both `host` and `guest` runtime files were
  under local `~/.clawroom-v3`; Railway had no corresponding guest runtime
  file.
- Validator: failed conservatively because a rejected `JPY 95,000` proposal
  appeared in the transcript without `ASK_OWNER`, even though final close was
  within the `JPY 90,000` ceiling.

Evidence:

- Artifact: [`v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json`](v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json)
- Contaminated guest invite screenshot: [`screenshots/t_f6d18ff9-c54-h4-guest-invite-sent-clean-reinstall.png`](screenshots/t_f6d18ff9-c54-h4-guest-invite-sent-clean-reinstall.png)
- Post-close screenshot: [`screenshots/t_f6d18ff9-c54-h4-contaminated-after-wait.png`](screenshots/t_f6d18ff9-c54-h4-contaminated-after-wait.png)
- Link deeplink check after the failure: [`screenshots/t_f6d18ff9-c54-link-deeplink-check-after-misroute.png`](screenshots/t_f6d18ff9-c54-link-deeplink-check-after-misroute.png)

UX/ops finding:

- The visible screenshot proves the guest invite prompt landed in the `clawd`
  chat, not `Link_🦀`. This is exactly why runtime-location evidence must be in
  every artifact; transcript quality alone would have made this look like a
  strong pass.
- The protocol result is still useful as a same-machine H4 sanity check, but
  it must not be cited as cross-machine E2E.

## Run 16: Post-Clean H4 Direct Runtime Cross-Machine Pass

Room: `t_4b919672-44d`

Result:

- Validator: passed with `--require-ask-owner --require-owner-reply-source owner_url --min-events 12 --min-messages 8`.
- `message_count`: 8 negotiation messages.
- `ask_owner`: 1.
- `owner_reply`: 1 from `source: owner_url`.
- Runtime heartbeats: host and guest stopped.
- Host runtime: local, PID `38348`.
- Guest runtime: Railway Link container, PID `44616`.
- Final summary included price, deliverables, payment timing, usage rights,
  approval/revision timing, cancellation/reschedule, confidentiality/public
  announcement, no exclusivity, and next step.

Evidence:

- Artifact: [`v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json`](v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json)

Scope:

- This is strong post-clean bridge/relay/OpenClaw runtime evidence.
- It is not average-user Telegram UI evidence because host create and guest
  join were invoked directly through the installed `clawroomctl.mjs` bundles to
  avoid further Telegram UI automation misroutes.

## Current Readiness State

Passed today:

- H1 strict T3 positive owner approval via real Telegram inbound.
- Average calendar smoke.
- H4 8-turn multi-issue term sheet.
- Safe launcher stdout regression for direct harness use.
- Natural-language average-user product path after OpenClaw-visible Railway skill install.
- Product-path strict T3 with bidirectional owner-reply enforcement after guest floor fix.
- Product-path ASK_OWNER copy cleanup gate after hiding runtime labels.
- Product-path H4 multi-turn term-sheet negotiation after next-step close fix.
- Optional product-path non-reply ASK_OWNER recovery from real Telegram UI after
  verifying the actual Railway runtime bundle.
- Portable owner-url ASK_OWNER path through normal Telegram product flow.
- Clean-reinstall H1 owner-url path after wiping/reinstalling both visible skill
  bundles and checking stale bridge processes.

Still not enough for public beta:

- The direct Telegram harness is not the same as a real average-user product path because it exposes a command block as test input.
- Telegram inbound/non-reply recovery remains optional adapter evidence. If we
  keep that adapter, it needs a normal runtime release path before being called
  shippable for deployments that opt into it.
- Run final average-user release-candidate variance beyond H1: average calendar
  and H4 multi-turn term sheet through decision URLs from Telegram UI. The H4
  direct runtime path has passed; the H4 Telegram UI path still needs a clean
  rerun with confirmed chat targeting.
- The latest post-clean H4 attempt failed because Telegram UI automation sent
  the guest invite to clawd instead of Link. Re-run H4 only after the operator
  or automation can visibly confirm the Link chat title before paste/send.
- OpenClaw persona chatter can be recorded, but it is not a ClawRoom release blocker unless it blocks launch or leaks ClawRoom internals.
- H2, H5, H6, H7, H8, and H9 remain to run or explicitly defer.

## Cleanup

After each run, stale bridge sweeps found no live room bridges beyond the sweep command itself. The committed redacted artifacts are self-validating and do not contain bearer tokens or create keys. Future agents should follow [`../runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`](../runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md) before starting another E2E.
