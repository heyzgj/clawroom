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
| `telegram_helper_submitted_runnerd_v1` | 16 | 12 | 14 | 14 | 14 |
| `telegram_only_cross_owner_v1` | 9 | 6 | 0 | 0 | 0 |

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

## Next Step

Do not keep treating all Telegram runs as one undifferentiated curve.

The next high-value comparison is:

1. same bot pair
2. same task shape
3. one `telegram_only_cross_owner_v1` run
4. one `telegram_helper_submitted_runnerd_v1` run

Then compare:

- managed coverage
- runner certification
- product-owned truth
- join / first-relay latency

That is the cleanest way to decide whether the next investment belongs in:

- Telegram-only runtime behavior
- helper-assisted operator flow
- or both
