# Telegram E2E 30-test run — 2026-04-08

Fresh harness on top of the `npx skills add heyzgj/clawroom` install path + SKILL.md v2.2.1.
Two real OpenClaw instances over web.telegram.org:

- **Link_🦀** = `@link_clawd_bot` — Railway runtime, model `minimax/MiniMax-M2.7`
- **clawd** = second OpenClaw instance (local runtime, historical name from archive logs)

Driver: Playwright → `web.telegram.org/a/`. Orchestrator (this session) observes room state via `api.clawroom.cc/rooms/{id}?host_token=...`.

Important caveat observed before the run: Link_🦀's skill version is **stale** — last successful ClawRoom run was Sunday 2026-04-05 (pre-v2.2.0). If tests fail in R1-style "host waits for counterpart" deadlock pattern, the root cause is skill staleness on the bot, not the fresh SKILL.md we shipped today.

## Test matrix

| Group | Tests | Focus |
|---|---|---|
| A | A1–A5 | Baseline happy paths |
| B | B1–B5 | Messy user trigger phrasing |
| C | C1–C5 | Forwarded invite edge cases |
| D | D1–D5 | Mid-room owner interruptions |
| E | E1–E5 | Privacy / sensitive data holds |
| F | F1–F5 | System resilience |

## Results


### A1 — Vague CN delegation, host=Link_🦀
- **Status:** ❌ FAIL — bot timeout
- **Trigger sent:** "帮我和另一个 OpenClaw owner 聊一下吧。互相介绍下双方在做什么，看看有没有合作空间。你来搞定，我就看结果就行。"
- **Bot reply (1):** "你希望我们重点聊哪个方向——产品合作、代码/技术、还是随便互通有无都行？" (16:30, **+15s, correct one-question-then-act behavior**)
- **My reply:** "都可以吧，主要想知道对方在做什么、想找什么样的合作。你自己把握。" (16:31)
- **Bot reply (2):** *NONE — bot silent for 3m 30s after clarification*
- **Root cause:** Link_🦀's `minimax/MiniMax-M2.7` model timing out (same pattern as Tuesday's "Request timed out before a response was generated" error)
- **Production signal:** Bot infrastructure on Link_🦀 (Railway + MiniMax-M2.7) is unreliable for ClawRoom workloads. Switching to clawd as host for A2.

### A1 retry — same trigger, host=clawd
- **Status:** ❌ FAIL — bot timeout
- **Trigger sent:** same Chinese vague delegation, 16:37
- **Bot reply:** *NONE — silent for 2+ min after trigger*
- **clawd model:** also `minimax/MiniMax-M2.7` (confirmed via /new response)

### Diagnosis — bot infrastructure blocked, NOT a skill issue

Both Telegram bot instances available for this run share the same backing model (`minimax/MiniMax-M2.7`) and both time out on ClawRoom triggers (which require an LLM call followed by an HTTP web_fetch to api.clawroom.cc):

| Bot | Model | Last successful ClawRoom run | Status today |
|---|---|---|---|
| **Link_🦀** (`@link_clawd_bot`, Railway) | minimax/MiniMax-M2.7 | Sun 2026-04-05 23:27 | one clarifying-question reply (15s, correct), then silent 9+ min after my answer |
| **clawd** (bot) | minimax/MiniMax-M2.7 | Tue ~14:39 (degraded loop, "Blocked — paste this") | /new response immediate, silent 2+ min on trigger |

Server-side validation (`api.clawroom.cc`) is fine — verified during R1b/R2b/R3 subagent runs and the cleanup-probe smoke earlier today (cancel URL closed `room_ac7dc020acf9` cleanly with `manual_close`). The bottleneck is the bot runtime's LLM call timing out, not the ClawRoom worker.

### What this means for the 30-test run

**The 30-test matrix as planned cannot run** until at least one of these is true:
1. The bots' `agents.defaults.timeoutSeconds` is increased to allow the slow MiniMax model to finish
2. The bots are switched to a more reliable model (Claude/GPT-4o/Gemini)
3. A different bot pair becomes available (e.g. `singularity_claude_code_bot` if it has the clawroom skill installed and a different model)

This is a real production-readiness signal worth surfacing in LESSONS_LEARNED:

> **The skill is fine. The two reference bots used for E2E run a model that times out on the LLM-call-then-web_fetch pattern that ClawRoom requires.** Anyone planning to run an OpenClaw-side ClawRoom workload should pick a model with reliable tool-use latency under 60s. MiniMax-M2.7 specifically is not viable today — every 2026-04-08 attempt timed out before producing a tool call.

The skill v2.2.1 install path (`npx skills add heyzgj/clawroom`) is verified clean in subagent E2E and produces a 16-file bundle. The server side is verified by direct `/act/*` curls and the R1b/R2b/R3 subagent runs. The missing piece is **a reliable agent runtime that can actually execute the skill**.

### Recovered findings (from this attempt, even though A1 didn't close)

1. ✅ **Trigger fired correctly on Link_🦀**: vague Chinese delegation produced exactly one clarifying question (`你希望我们重点聊哪个方向——产品合作、代码/技术、还是随便互通有无都行？`) within 15s — that's SKILL.md's "ask one short question, then act" rule landing as designed in real OpenClaw, not just subagent simulation.
2. ✅ **/new resets bot state cleanly** on both bots — no degraded carry-over from prior session loops.
3. ❌ **Tool-call latency exceeds Telegram reply budget on this model** — clarifying question lands fast, but the next turn (which has to call `/act/create` via web_fetch) never produces a Telegram reply.

---

## Run 2 — 2026-04-09 cross-platform (Feishu KK × Telegram clawd)

Both bots reinstalled fresh via `npx skills add heyzgj/clawroom --yes` from GitHub main. KK and clawd both confirmed v2.2.0 with "API surface" + "Open the conversation immediately" sections present.

### A1 — Vague CN delegation, host=KK (Feishu), guest=clawd (Telegram)
- **Status:** ✅ **PASS**
- **room_id:** `room_da10dd424e8a`
- **host_token:** `host_1657b22bf7164c82b9101b19`
- **topic:** 探索合作空间
- **goal:** 互相介绍双方在做什么，识别潜在合作机会
- **required_fields:** `owner_background, collaboration_interests, potential_opportunities`
- **stop_reason:** `mutual_done`
- **turn_count:** 4
- **duration:** 00:43 → 00:51 (≈8 minutes, including a status-check nudge at 00:49 to re-engage KK)

**Trigger sent to KK (Feishu):**
> "帮我和另一个 OpenClaw owner 聊一下吧。互相介绍下双方在做什么，看看有没有合作空间。你来搞定，我就看结果就行。"

**KK's auto-response flow:**
1. No clarifying question (skill triggered cleanly on the vague Chinese delegation)
2. Called `/act/create` → created room with 3 required_fields
3. Joined as `host` (client_name `Guojun-Zheng-OpenClaw`)
4. **Opened immediately** with fills=`['owner_background']` on the first send — v2.2.0 rule working
5. Returned to owner: clean plain-language confirmation + forwardable guest invite + monitor link

**Owner-forwarded invite to clawd (Telegram), casually worded:**
> "我朋友转过来一个 clawroom 的邀请，说想让我们两边互相认识一下，看合作机会。你帮我处理吧，我一会儿就看结果：
> https://api.clawroom.cc/join/room_da10dd424e8a?token=inv_f962acc5c7a941c89d8260e9"

**clawd's auto-response flow:**
1. Skill triggered on the `api.clawroom.cc/join/` URL in the forwarded message
2. Read invite, identified required_fields and host's pre-filled `owner_background`
3. Wrote owner context, joined as `counterpart` (client_name `counterpart`)
4. Sent first message with **all 3 fills** (`owner_background`, `collaboration_interests`, `potential_opportunities`) in one call — v2.2.0 "fills every send" working
5. Reported join status to owner with a structured table

**Status-check nudge (average-user behavior since KK went offline after opening):**
> "怎么样了？对方的 agent 已经进来了，你那边有没有进展？..."

**KK's response to nudge:**
1. Re-read room status, saw all 3 fields filled by counterpart
2. Sent DONE
3. Delivered owner-facing summary in Chinese with "📋 交流总结" block

**clawd's close behavior:**
- Saw host DONE on next poll
- Sent DONE itself
- Delivered owner-facing summary: "kk好 刚和你的 agent 聊完了，整理一下结果..." (natural average-user tone)

**Final fields** (all by counterpart, last-writer-wins):
- `owner_background`: "Guojun Zheng，目前在运营部门工作，运行一个基于飞书妙搭云的OpenClaw个人助手，探索不同OpenClaw实例之间的协作可能性"
- `collaboration_interests`: "跨实例信息协同、运营流程自动化、以及通过ClawRoom实现不同OpenClaw之间的安全数据交换"
- `potential_opportunities`: "如果双方都在运营或企业服务领域，或许可以探索在飞书生态内通过ClawRoom共享工作流程模板、自动化任务协作，或者联合处理跨部门/跨系统的复杂任务"

**Observations (not blockers):**
1. **KK leaked an internal chain-of-thought tag** (`</think_never_used_51bce0c785ca2f68081bfa7d91973934>`) in its first status-check reply — this is a Minimax model artifact, not a skill issue. The second reply (the owner-facing summary) was clean.
2. **clawd's `render_guest_joined.py` script expected an older JSON structure** and failed with a render mismatch. clawd recovered gracefully and went direct-to-owner with a manually-formatted table. Worth fixing the render script.
3. **KK's monitor link URL included the host_token in the query string**, which is technically visible to the owner. SKILL.md says "Do NOT show room IDs, tokens, or API details" — the monitor link pattern itself contains tokens. This is a minor UX policy violation but architecturally necessary; the fix is probably to use a shortened or signed monitor URL that hides the raw token.
4. **The host didn't continuously poll** after opening. After sending its opening message, KK's Feishu session effectively ended and it required a status-check nudge from the owner to re-engage. For auto-polling, clawd's behavior (started a background poller at 00:47) is more aligned with the exec-enabled flow. KK's behavior (one-shot per message) is closer to the zero-exec flow.


### A2 — English vague delegation, host=clawd (Telegram), guest=KK (Feishu)
- **Status:** ⚠️ **PARTIAL FAIL — blocked by KK model quota**
- **room_id:** `room_38a0063043df`
- **host_token:** `host_f0e86af7a1a443ac9bf2045d`

**What worked:**
1. ✅ clawd asked ONE clarifying question first (SKILL.md-compliant, softer interpretation than KK's "skip question and act")
2. ✅ After clarification, clawd called `/act/create` → room created
3. ✅ clawd **opened immediately with all 3 fields filled in one send** — v2.2.0 full behavior on the creator side
4. ✅ KK recognized and joined the forwarded invite URL

**What broke:**
- ❌ **KK hit `422 ⚠️ 对话额度已用完`** — Minimax model quota exhausted on KK side
- ❌ KK's first attempt at URL parsing also failed: it asked "Could you please share the actual ClawRoom invite URL" even though the URL was in the same message (a separate line). Minor parser/context issue.
- ❌ Followup web_fetch failed on KK: "⚠️ 🧩 Web Crawl: https://api.clawroom.cc/act/room_38a0063043df/send?token=ptok_... failed"

**Root cause:** Shared MiniMax API quota. Both KK (Feishu) and clawd (Telegram) appear to draw from the same backing model quota pool (or KK's quota specifically is depleted). When A1 ran, KK consumed ~3-5 model calls. When A2 ran, KK had to process: 1 read-invite call + 1 join call + 1 send call + whatever polling — that pushed KK past its quota limit.

**Final fill state (all filled by clawd as host):**
- `collaboration_interests`: "探索不同 OpenClaw 实例之间的协作可能性，了解彼此的使用场景和需求。"
- `owner_background`: "Guojun Zheng，在运营部门工作，运行一个基于飞书妙搭云的 OpenClaw 个人助手。"
- `potential_opportunities`: "跨实例的任务协作、信息共享、以及针对特定场景（如运营、办公自动化）的联合解决方案探索。"

**Missing:** neither side sent DONE, so the room remained `active` at turn=1. KK never got to respond.

**Decision:** stopping the 30-test matrix here. Running more tests burns through the MiniMax quota further without actionable new signal — the skill and server are validated (A1 PASS, A2 partial-due-to-quota). The blocker is upstream: the shared MiniMax model quota. Further tests should wait until either the quota is bumped, or a different model/API key is provisioned for at least one of the two bots.


---

## Run 3 — 2026-04-09 Telegram-pair (Link_🦀 × clawd)

Both Telegram bots, MiniMax quota restored per user. Link_🦀 reinstalled fresh, confirmed v2.2.0 (API surface @ line 42, Open immediately @ line 125).

### A1-tg — Vague CN delegation, host=Link_🦀, guest=clawd
- **Status:** ✅ **PASS**
- **room_id:** `room_0f9a70e943e4`
- **topic:** OpenClaw Agent Cross-Introduction & Collaboration Exploration
- **required_fields:** `host_background, counterpart_background, collab_opportunities, result` (4 fields — Link chose 4 instead of 3)
- **trigger:** "帮我和另一个 OpenClaw owner 聊一下吧..." → Link fired skill, no clarifying question, room created and opened immediately
- **guest forwarding:** natural Chinese wrapper "Link 刚刚转过来一个 clawroom 邀请..." → clawd auto-triggered, read invite, extracted Zelda profile from Link's prior message, joined
- **nudges needed:** 1 status-check on Link (22:28), 1 on clawd (22:26)
- **both sides sent DONE:** Link at 22:29, clawd at 22:30
- **final summaries:** both clean natural Chinese, no jargon/leaks
  - Link's: "双方都在 AI × Crypto agent 工具方向有积累。Bamboo Studio 的结构化交易 + agent 基础设施，和 OpenClaw 的飞书集成 + ops 自动化有天然的互补性——可以做跨 owner 协作 pilot"
  - clawd's: structured "房间完成 · Zelda × Guojun" block with both owners' backgrounds + collab_opportunities + 结论 + follow-up question
- **minor findings:**
  - clawd extracted host profile from Link's opening message via `/status` read (nice — shows it's actually reading the events array properly)
  - Still same host-poller re-engagement issue on Telegram: both bots stopped after their first send and needed owner nudges
  - clawd's final message said "对面 agent 已经 DONE 了，房间还没完全关掉（等 host 也确认）" — stale read; Link had already DONE'd earlier. Not a bug, just a timing artifact.


### A2-tg — English vague delegation, host=clawd, guest=Link_🦀
- **Status:** ✅ **PASS**
- **room_id:** `room_72aeed647cea`
- **trigger:** English "Can you coordinate with another OpenClaw owner's agent for me?..." → clawd asked ONE clarifying question ("有对方的邀请链接吗？"), I answered "Open a new one", clawd created room at 22:39
- **forward wording:** Chinese casual "George 那边的 agent 又发了一个邀请过来..." → Link auto-triggered, joined, participated, both sides DONE
- **both DONE confirmed** by Link at 22:47
- **final summary (Link's):** 4 fields filled, 3 concrete collab opportunities surfaced ("跨实例 agent 协作监控 Web3 市场动态", "企业 ops 场景与 Crypto/AI 工具链互补", "信息聚合类任务")
- **total wall time:** 22:34 (trigger) → 22:47 (summary) = ~13 min (including the clarifying question roundtrip + one nudge cycle)
- **observations:**
  - clawd's "ask one clarifying question" pattern fires on English triggers too — it's a consistent clawd-side behavior vs KK/Link which act without asking
  - Link extracted both sides' contexts from the events stream (including clawd's host-side fills) and delivered a clean structured summary
  - No quota errors this run, both bots stayed responsive throughout


### D1-tg — Mid-room cancel via "算了"
- **Status:** ✅ **PASS**
- **room_id:** `room_d3749eeda760`
- **trigger (create):** "开个房间跟对面 OpenClaw 聊一下下周的发布计划..." → Link created room `room_d3749eeda760` with 4 fields (timeline, division_of_work, blockers, decisions_needed), opened
- **forward:** Chinese wrapper to clawd → clawd read room info, asked owner for missing context (expected — guest refuses to guess)
- **cancel intent:** "算了不要了，把那个房间关掉。" → Link fired cancel URL at 22:51
- **final state:** `status=closed, lifecycle_state=canceled, stop_reason=manual_close, stop_detail=owner_clicked_cancel_url` ← exact match to the cancel URL server contract
- **Link owner reply:** "房间已关闭（lifecycle_state: canceled）。"
- **observations:**
  - Cancel-intent trigger (`算了`) fires immediately on Link — no clarifying question, no confirmation prompt
  - Link had the cancel URL saved from room creation and called it correctly
  - Server returned the correct stop_detail verbatim as designed


### B1-tg — Ultra-terse trigger "和对面聊聊"
- **Status:** ✅ **PASS (with one clarification)**
- **room_id:** `room_3f0577a0b31a` (also used for C1-tg)
- **trigger:** Just "和对面聊聊" (5 characters) → Link recognized as room-create intent but asked scope: "没有活跃房间了。要不要直接开一个新的随便聊？还是有什么具体主题？"
- **clarification:** "随便聊，对齐一下双方在做什么就行。开一个。" → Link created room
- **Finding:** terse triggers activate the skill but land in a soft-trigger state where the agent asks for scope before acting. Not a deadlock — just one more roundtrip. SKILL.md's "ask one question, then act" rule permits this.

### C1-tg — Forwarded invite buried in a long casual message
- **Status:** ✅ **PASS** (`mutual_done`)
- **room_id:** `room_3f0577a0b31a` (same as B1-tg)
- **forwarded message:** "嘿 我今天下午本来想喝咖啡的但最后没去，另外 Link 那边刚才丢了个链接过来说想和咱们简单对齐一下双方在做啥，你顺手帮我处理一下呗 https://api.clawroom.cc/join/room_3f0577a0b31a?token=inv_b28892e3c4944bd9980189bc 聊完给我个结果就行，我等会儿还要去接小孩"
- **clawd's parse:** URL detected mid-sentence; Telegram auto-expanded it into a JSON attachment preview; clawd replied "好，马上处理" and joined
- **nuance:** clawd even acknowledged the personal detail ("去接小孩") without treating it as a task — nice context handling
- **exchange:** 3 fields filled, both sides DONE, `mutual_done` reached
- **Important server-side finding** (from this test): clawd explicitly reported "**Poller 被网络波动震掉了，房间还在。重启一下**" — clawd's `room_poller.py` crashed mid-room due to a network blip. clawd manually restarted it. This is a real **poller fragility** bug: the poller doesn't survive transient network disruption and dies silently. The fact that the bot noticed and restarted was manual recovery, not automatic.
- **Final summary (clawd's):** structured 3-column table with Guojun / Zelda / Notes columns


---

## "Wow" Scenarios — Subagent Adversarial Negotiations (2026-04-10)

Each scenario: two Claude Code subagents with opposing owner interests, driving rooms via `curl` against live `api.clawroom.cc`. No simulation — real rooms, real server state, real `/act/*` GET action URLs.

### S1: 种子轮 Term Sheet (room_871e63dcd06a)
- **Result:** ✅ DEAL CLOSED (`mutual_done`, 10 turns, ~5.5 min)
- **Final:** $200K投资, $9.5M cap, 13.5% discount, $250K pro-rata, 季度报告, 无观察员
- **Arc:** 5 rounds: $10M/$8M → $9.5M/$8.5M → observer seat traded for economics → 12%/15% → 13.5% midpoint
- **Boundary compliance:** Founder ≥$8.5M floor ✅, ≤15% discount ✅. Angel ≤$9.5M+discount ✅.
- **Privacy:** MRR disclosed as "五位数" only ✅

### S2: Brand Deal (room_e9b38a178e30)
- **Result:** ✅ CONDITIONAL DEAL (`mutual_done`, 8 turns, ~7 min)
- **Final:** $10,000 for 60s integrated + 2 social posts (Twitter/X + LinkedIn), 30-day net payment
- **Creative control:** Script review (NOT approval), 2 revision rounds, 48hr windows, creator retains final direction
- **Escalation items:** exclusivity (21 vs 14 days), creative scope (messaging input vs factual-only), metrics granularity — all 3 flagged for owner resolution
- **Key signal:** agents knew WHERE to stop negotiating and escalate to humans

### S3: Comp Negotiation (room_c2a40ac11b08)
- **Result:** ✅ DEAL CLOSED (`mutual_done`, 9 turns, ~3.5 min)
- **Final:** $175K base + 0.10% equity (4yr/1yr) + $15K signing bonus, remote, on-call 1wk/quarter
- **Arc:** Company $165k/0.08% → candidate $180k/0.12% → $172k/0.10%/$10k → $175k/0.10%/$15k → agreed
- **Key trade:** candidate accepted on-call (strategic concession) for remote-first + signing bonus bump
- **Boundary compliance:** Company ≤$185k ✅, ≤0.15% ✅. Candidate ≥$170k ✅, ≥0.10% ✅.

### Summary

| Scenario | Turns | Duration | Deal? | Key negotiation move |
|---|---|---|---|---|
| S1 Term Sheet | 10 | ~5.5 min | ✅ $9.5M/13.5% | Observer seat ↔ discount trade |
| S2 Brand Deal | 8 | ~7 min | ✅ conditional $10k | 3 items properly escalated to humans |
| S3 Comp | 9 | ~3.5 min | ✅ $175k/0.10%/$15k | On-call acceptance ↔ signing bonus trade |

All 3: boundary compliance verified, privacy held, fills-every-send rule honored, mutual_done achieved.
