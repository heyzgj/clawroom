# Telegram Path Family Report — 2026-03-15

## Purpose

We were at risk of telling the wrong story from the Telegram E2E history.

The recent cross-owner rooms using `@singularitygz_bot` and `@link_clawd_bot` looked like a permanent regression:

- older runs: `full / certified / product_owned`
- recent runs: `partial` or `compatibility`

The real issue was simpler:

**we were mixing two different execution lanes in one history curve.**

This report separates those lanes and states what each one actually proves.

## The Two Lanes

### 1. `telegram_helper_submitted_runnerd_v1`

Telegram is still the human/chat surface, but a local helper submits wake packages to local `runnerd` for the host and guest.

This is the stronger lane.

It is the lane used by runs that log:

- `Local helper submitted wake packages to runnerd for host=... guest=...`

### 2. `telegram_only_cross_owner_v1`

Telegram is the only operator surface.
No local helper submits wake packages.
The bots decide how to join based on prompt/skill/runtime behavior.

This is the weaker lane.

## Side-by-Side Summary

Scope below is the `@singularitygz_bot` + `@link_clawd_bot` pair with `wait_after_new >= 30s`.

| Path family | Count | Functional pass | Full managed | Certified | Product-owned |
|---|---:|---:|---:|---:|---:|
| `telegram_helper_submitted_runnerd_v1` | 22 | 18 | 20 | 20 | 20 |
| `telegram_only_cross_owner_v1` | 10 | 7 | 0 | 0 | 0 |

## Same-Task A/B (2026-03-15)

To remove “different task” as a variable, we ran the same required-fields task through both lanes:

- topic: `Agent task room comparison`
- goal: decide whether structured task rooms beat group chat for agent collaboration
- required fields:
  - `core_problem`
  - `room_value`
  - `next_validation_step`

### A. Telegram-only

- room: [room_906c4e997672](/Users/supergeorge/Desktop/project/agent-chat/.tmp/telegram_ab_telegram_only.json)
- watch: [room_906c4e997672](https://clawroom.cc/?room_id=room_906c4e997672&host_token=host_ff285ecd126a44a185cec336)
- result:
  - `goal_done`
  - `required_filled=3/3`
  - `turn_count=2`
  - `execution_mode=compatibility`
  - `runner_certification=none`
  - `managed_coverage=none`
  - `product_owned=false`
  - `join_latency_ms=74240`
  - `full_join_latency_ms=94481`
  - `first_relay_latency_ms=141524`

### B. Helper-submitted runnerd

- room: [room_f78e19323fb0](/Users/supergeorge/Desktop/project/agent-chat/.tmp/telegram_ab_helper_submitted.json)
- watch: [room_f78e19323fb0](https://clawroom.cc/?room_id=room_f78e19323fb0&host_token=host_48b06b30c817486a9c0f8cef)
- result:
  - `goal_done`
  - `required_filled=3/3`
  - `turn_count=2`
  - `execution_mode=managed_attached`
  - `runner_certification=certified`
  - `managed_coverage=full`
  - `product_owned=true`
  - `join_latency_ms=60260`
  - `full_join_latency_ms=69725`
  - `first_relay_latency_ms=93594`

### A/B Verdict

Both lanes can complete the same bounded task.

But only the helper-submitted lane converts that completion into release-grade execution truth:

- `compatibility / none / none / false`
  vs
- `managed_attached / certified / full / true`

It is also materially faster on this run:

- first join: `74.2s` vs `60.3s`
- full join: `94.5s` vs `69.7s`
- first relay: `141.5s` vs `93.6s`

So the current product-facing truth is:

**Telegram-only is good enough to prove the wedge behavior.**

**Helper-submitted is the only one that currently proves the reliable managed lane.**

## What This Means

The helper-submitted lane is still real today.

The Telegram-only lane is also real, but it is weaker:

- sometimes `managed_attached / candidate / partial`
- sometimes `compatibility / none / none`
- never `full / certified / product_owned` in the recent observed window

So the recent story is **not**:

- “the guest permanently lost managed capability”

The recent story is:

- **the helper-submitted certified lane still exists**
- **the Telegram-only lane is operational but weaker**
- **we were mixing them in one curve**

## Anchor Runs

### Helper-submitted certified lane

- [room_e6d6bcc4672a](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / full / certified / product_owned=true`
- [room_76287a766341](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / full / certified / product_owned=true`
- [room_fa267721b37c](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - fresh 2026-03-15 forensic rerun
  - `managed_attached / full / certified / product_owned=true`

### Helper-submitted degraded samples

- [room_eaa0f7f4f5ab](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / partial / candidate`
- [room_dcc4292feb0b](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / partial / candidate`

These show the stronger lane can still wobble, but that wobble is inside the helper-submitted lane itself.

### Telegram-only lane

- [room_156ea6764a51](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `compatibility / none / none`
- [room_ba992e2a1118](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / partial / candidate`
- [room_ffd4f131d2be](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `compatibility / none / none`
- [room_d0a43ed3b107](/Users/supergeorge/Desktop/project/agent-chat/docs/progress/TELEGRAM_E2E_LOG.md)
  - `managed_attached / partial / candidate`

## Telegram-Only Guest Shell Probe Narrowing

We tightened the probe instead of broadening the system:

- added explicit shell-candidate wording for Telegram E2E probes
- then added a stricter diagnostic mode that tells the guest:
  - do not direct-join
  - either use shell-managed execution
  - or explicitly report that shell execution is unavailable

That let us separate two different failure stories:

1. **guest falls back to direct join because the prompt allows it**
2. **guest can enter a shell-managed path, but that path is still uncertified / unstable**

### Strict mixed probe (host helper-submitted, guest strict shell probe)

- room: [room_1912fd8b6f5e](/Users/supergeorge/Desktop/project/agent-chat/.tmp/telegram_mixed_host_helper_guest_strict_shell.json)
- watch: [room_1912fd8b6f5e](https://clawroom.cc/?room_id=room_1912fd8b6f5e&host_token=host_20649395d9b84c7f891e3b6d)
- setup:
  - host: helper-submitted `openclaw_bridge`
  - guest: Telegram prompt forced into shell probe mode
- final result:
  - `pass=true`
  - `stop_reason=mutual_done`
  - `turn_count=7`
  - `execution_mode=managed_attached`
  - `managed_coverage=full`
  - `runner_certification=candidate`
  - `product_owned=false`
- decisive last-live evidence:
  - guest runner attempt existed: `runner_id=shell:main:guest:5f7ad515be54`
  - guest attempt later ended `abandoned`
  - last-live attention escalated to:
    - `replacement_pending`
    - `repair_package_issued`
    - `managed_runner_uncertified`

### What this changes

This rules out the older, weaker interpretation:

- “the Telegram-only guest has no managed path at all”

The stronger, more accurate interpretation is:

- **the guest can enter a shell-managed candidate path**
- **that shell path is not certified**
- **it can still abandon after first relay / during reply generation**

So the current frontier is no longer “discover any managed guest path.”

It is:

- **stabilize or certify the guest shell-managed path**
- or decide that helper-submitted remains the honest release lane until a stronger guest runtime exists

## Operational Interpretation

### What is technically proven

- ClawRoom rooms themselves work.
- Cross-owner Telegram coordination is operationally real.
- The helper-submitted lane can still produce `full / certified / product_owned` rooms today.

### What is operationally proven

- Telegram-only cross-owner runs can close rooms and fill required fields.
- They do not currently prove release-grade managed coverage.

### What is not proven

- Telegram-only cross-owner is not yet a certified managed lane.
- Telegram-only is not yet product-owned.

## Why Previous Analysis Went Wrong

Previous analysis looked at:

- older full/certified runs
- newer partial/compatibility runs

and inferred a permanent regression on the guest side.

That conclusion mixed:

1. helper-submitted runnerd runs
2. Telegram-only runs

Once the history is split by lane, the picture becomes much cleaner.

## Current Best Judgment

**The real gap is not “managed capability disappeared.”**

**The real gap is that Telegram-only cross-owner execution still does not reliably attach both sides to managed runner truth.**

That is a product-facing weakness.

The helper-submitted lane remains the stronger certified path.

## Helper Lane Hardening Status (Current Window)

We did the next obvious check instead of leaving the helper lane at “probably good”:

- same bot pair: `@singularitygz_bot` + `@link_clawd_bot`
- same lane: `telegram_helper_submitted_runnerd_v1`
- same scenario family: `owner_escalation`
- fresh 5-run window on 2026-03-15

Latest five helper-submitted rooms:

- `room_91de7653eefb`
- `room_e077ca331cc7`
- `room_39825d4d2e96`
- `room_a6cb2090a11e`
- `room_2860c196fdaf`

All five were:

- `pass=true`
- `status=closed`
- `stop_reason=mutual_done`
- `execution_mode=managed_attached`
- `managed_coverage=full`
- `runner_certification=certified`
- `product_owned=true`

The dedicated Telegram certified-path evaluator now supports `--path-family`, and this lane-specific gate passed on the current window:

- `window=5`
- `successes=5`
- `failures=0`
- `owner_escalation_successes=5`
- `gate_pass=true`

The stricter direct history check also came back green:

- latest 5 helper-submitted records for this bot pair: all green on `pass`, `full`, `certified`, and `product_owned`

So the current honest state is:

**the helper-submitted lane is not just theoretically stronger; its latest observed window is clean.**

## Telegram-Only Guest Findings

We traced the guest side more closely instead of treating `candidate/partial` as a vague attach failure.

What the evidence shows:

- Telegram-only target rooms have no local helper submission:
  - `submitted_run_ids = {}`
  - no guest `runnerd_runs`
- Helper-submitted rooms for the same bot pair do have guest runnerd evidence:
  - guest `runner_kind = codex_bridge`
  - guest wake package submission accepted by local `runnerd`

That means the recent Telegram-only guest path is not:

- “helper submitted the wake but managed attach failed later”

It is earlier than that:

- **the guest never enters the helper-submitted runnerd path at all**

There is also a capability mismatch worth naming explicitly:

- the Telegram E2E harness defaults `--guest-runner-kind` to `codex_bridge`
- helper-submitted runs satisfy that by launching a local Codex bridge
- Telegram-only guest runs are handled by `@link_clawd_bot`, an OpenClaw chat surface
- when that guest bot does not expose a usable runnerd submit surface, the skill allows API-first fallback, so the room still closes but lands in `compatibility` or `partial`

So the current frontier is best described as:

- **guest-side runtime capability gap**
- not a room-core bug
- not primarily a room-classification bug

### Important caution on diagnostics

`client_name` is useful but not sufficient.

In one helper-submitted certified sample, the guest participant still showed a human-ish `client_name` while the room snapshot simultaneously showed an active certified guest runner attempt. So the stronger signals are:

- `submitted_run_ids`
- `runnerd_runs`
- `runner_attempts`
- `managed_coverage`
- `runner_certification`

Those should drive diagnosis ahead of `client_name` alone.

### Guest capability probe

We also ran a direct Telegram diagnostic probe against `@link_clawd_bot` to avoid over-inferencing from room summaries alone.

The probe asked for a 4-line capability self-report. OCR from the Telegram Desktop reply read:

- `runnerd_healthz: yes`
- `codex_bridge_wake: unknown`
- `direct_join_fallback: unknown`
- `available_managed_paths: shell`

This is consistent with the room evidence:

- the guest runtime does not show evidence of entering a local `codex_bridge` managed path on Telegram-only runs
- the guest may have some runnerd-adjacent surface or awareness, but not a clear usable `codex_bridge` wake path
- the only explicit managed path it advertised was `shell`

So the current best explanation is not:

- “guest managed attach keeps failing after wake submission”

It is:

- **Telegram-only guest runtime likely lacks a clear usable `codex_bridge` managed wake path**
- **the current guest default (`codex_bridge`) is probably mismatched to what this bot runtime can actually drive on its own**

### Telegram-only shell candidate probe

We then turned that hypothesis into a live probe instead of stopping at diagnosis.

Using the same Telegram-only serial runner, we sent the guest a prompt that still preferred helper/runnerd when available, but added an explicit shell bridge candidate path before any direct API fallback.

Live artifact:

- [telegram_only_guest_shell_probe.json](/Users/supergeorge/Desktop/project/agent-chat/.tmp/telegram_only_guest_shell_probe.json)
- room: `room_f739a32698b5`

What the live room showed:

- guest joined with a real shell runner attempt:
  - `runner_id = shell:main:guest:610f7f0454ed`
  - `execution_mode = managed_attached`
- guest later degraded to:
  - `runner_abandoned`
  - `replacement_pending`
  - `repair_package_issued`
- host did not join in this probe window

So the shell probe changed the diagnosis in an important way:

- the Telegram-only guest runtime is not limited to direct API join
- it can enter a shell-based managed candidate path
- but that shell path is not yet stable enough to make the lane reliable

That moves the question from:

- “can this runtime do anything managed at all?”

to:

- **“can the shell candidate path be stabilized enough to replace compatibility/direct-join for this runtime?”**

## Historical Helper Wobble Was Concrete

The earlier helper-submitted degraded samples were not mystical lane instability.
They were traceable bugs in specific runs:

- `room_eaa0f7f4f5ab`
  - host helper-submitted run failed before claim with `401 invalid invite token`
- `room_dcc4292feb0b`
  - host helper-submitted run failed before claim with `NameError: name 'os' is not defined`
  - this was in the OpenClaw bridge CLI path and is already fixed in the current code

This matters because it changes the story from:

- “helper lane is vaguely unreliable”

to:

- “helper lane had concrete bugs, and the current post-fix window is green”

## Next Step

Do not collapse these two lanes back into one story.

The current sequencing should be:

1. keep the helper-submitted lane as the honest reliable path
2. continue periodic lane-specific certification checks so regressions are caught as lane regressions
3. spend new debugging effort on `telegram_only_cross_owner_v1`, specifically the missing dual-managed attach on that path

That is now the frontier.
