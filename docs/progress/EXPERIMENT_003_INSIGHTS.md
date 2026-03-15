# Experiment #003 Insights: What We Actually Learned

**Date**: 2026-03-14
**Covers**: Dogfood_001, Experiment_002, Experiment_003

---

## The 7 Insights

### 1. Infrastructure is validated. Product experience is not.

Room Core, Runner Plane, Recovery, Cross-runtime — all proven through real execution (5 rooms certified, 3+ auto-replacements, Railway MiniMax + local Claude collaborating). But the CEO has nothing useful to look at. The monitor dashboard shows agent chat transcripts, not mission outcomes.

**Evidence**: Test D was "READY FOR MANUAL EVALUATION" but the dashboard shows a message timeline, not "3 of 5 tasks done, here are the results."

### 2. The dashboard reflects the wrong mental model.

| Current | Needed |
|---------|--------|
| "Watch agents chat in a room" | "Watch work get done" |
| Timeline of messages + agent orbs | Mission progress + task cards + filled outcomes |
| Outcome summary is tiny, shown after close | Outcomes are the primary content |

The Missions Dashboard in the monitor is a skeleton with zero rendering logic (4 metric cards, empty panels, no data fetching for missions). The Room view is a hybrid — chat during execution, outcome summary after close — but the outcome display is secondary to the transcript.

### 3. ClawRoom's differentiators are proven but invisible in the UX.

| Differentiator | Proven? | Visible to CEO? |
|---|---|---|
| Bounded execution (turn limits) | Yes — all rooms closed within limits | No — turn count is just a small number |
| Structured outcomes (required_fields) | Yes — 5 rooms filled fields | Barely — shown in small summary after close |
| Execution supervision (recovery) | Yes — 3+ auto-replacements | No — happens in runnerd, invisible to dashboard |
| Cross-runtime | Yes — Railway MiniMax + local Claude | No — indistinguishable from single-runtime in UI |
| Certification | Yes — all completed rooms certified | No — badge exists but meaning unclear |

**The punchline**: Everything that makes ClawRoom different from Paperclip/Symphony/A2A is invisible to the person it's supposed to impress.

### 4. Built does not equal Validated.

MissionDurableObject and TeamRegistryDurableObject are deployed to Cloudflare. Zero production calls. Zero tests. The clawroom-lead skill describes calling `POST /missions` and `POST /missions/{id}/tasks`, but Experiment #003 bypassed it entirely — Claude Code just created rooms directly.

**The question**: Does the CEO need a mission API, or is "lead agent creates rooms and assembles results" the actual pattern? Experiments suggest the latter works fine without mission infrastructure.

### 5. The "last mile" problems are product problems, not infra.

| Problem | Category | Root Cause |
|---|---|---|
| Agents say "see you later" and stall | Prompt engineering | Bridge prompt lacks "never defer" guidance |
| Dashboard doesn't tell CEO story | Product design | Monitor designed for debugging, not outcomes |
| Two-sided wake requires two prompts | UX | Bot should auto-wake both sides |
| Wake-up via Telegram fragments JSON | Integration UX | Telegram message limits |

None of these are infrastructure blockers. They're all product/UX/prompt design issues.

### 6. The permission problem is real and unsolved.

From Dogfood_001: 5 parallel worker agents all failed because they couldn't write files. Background agents cannot escalate permission requests. ASK_OWNER exists in the room protocol but hasn't been tested as a structured permission escalation channel.

The CEO dream requires workers that can actually DO things, not just chat. The delegation pattern breaks if workers can't act.

### 7. The project is at the end of what infrastructure alone can prove.

The engineering assessment says it directly: "The first wedge is less crisp than the engineering foundation." Execution truth is now validated. The next layer demands product decisions:
- What does a CEO actually want to see?
- Is "mission -> rooms -> outcomes" the right abstraction?
- Should the product surface be a dashboard, a Telegram bot, or both?

---

## The Full Picture

```
VALIDATED (can demo live)          BUILT BUT UNTESTED           NOT BUILT
-----------------------------     --------------------          ---------
Room Core                         MissionDO                    Outcome-centric dashboard
  - lifecycle, turns, timeouts      - init, add task, complete  Mission progress view
  - required_fields enforcement     - task status updates       Cost tracking per room
  - result extraction               TeamRegistryDO              Agent capability cards
Runner Plane                        - agent registration        Permission manifest
  - wake, attach, heartbeat         - heartbeat                 Multi-lead coordination
  - auto-restart, auto-replace      - assignment inbox          Project-level ops view
  - agent pool (6 agents)         Missions Dashboard stub
  - round-robin assignment          - 4 metric cards, no data
Cross-Runtime
  - Railway MiniMax + local Claude
  - Telegram bot -> API -> runnerd
Recovery
  - 3+ auto-replacements
  - repair invite reissue
Certification
  - certified/candidate grading
```

**Strategic position**: Strong execution substrate. Zero product surface for the person who matters (the CEO/owner). The thing that differentiates ClawRoom from competitors is invisible.

---

## CEO Dream Scoreboard

```
CEO tells agent a goal
    -> Agent decomposes into tasks       [PROVEN] Test B
    -> Creates rooms with requirements   [PROVEN] Test B + Test E
    -> Assigns to workers' agents        [PROVEN] Test B (runnerd) + Test E (bot -> runnerd)
    -> Workers from different runtimes   [PROVEN] Test C (Railway MiniMax + local Claude)
    -> CEO watches dashboard             [NOT TESTED] Dashboard is chat-centric
    -> Gets assembled results            [PROVEN] Test B (3-room deliverable)
```

5 of 6 steps proven. The missing step is the product experience.

---

## What Competitors Would See

If Paperclip, Symphony, or A2A teams looked at ClawRoom today:

- **Paperclip** would see: "They have bounded execution and structured outcomes we don't. But their dashboard is worse than ours — we show goal hierarchies, they show chat logs."
- **Symphony** would see: "They support cross-runtime, which we don't. But we have stall detection and retry built in. Their agents stall with 'see you later.'"
- **A2A** would see: "They built the execution layer our protocol doesn't define. But they have no agent discovery or capability negotiation."

**The differentiators are real. The product surface doesn't show them.**

---

## Next Move

Design-first approach: mockup the CEO experience before building it. The question to answer is whether "Mission -> Tasks -> Outcomes" is the right mental model, or if a CEO just wants "I asked for X, is it done yet?"
