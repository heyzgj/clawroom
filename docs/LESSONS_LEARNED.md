# ClawRoom Lessons Learned

A running document of every experiment, every pitfall, every "we thought X would work but Y happened" moment from building ClawRoom. The goal: never re-learn the same lesson twice.

Last updated: 2026-04-07

---

## Meta-Lesson: The One Rule

> **Never trust the LLM with completion authority.**

Every reliability win in this project came from moving completion authority OFF the LLM and onto code we wrote. Every reliability loss came from believing prompt rules would be followed.

The corollary: when the LLM says "I did X", verify X happened. When the LLM says "I'll do X next time", assume it won't. When you give the LLM a rule, write the rule into a state machine, not a prompt.

---

## Part 1: LLM Unreliability Patterns

These are the actual failure modes we hit. Each is a category, with concrete instances.

### A. Phantom Completion — LLM claims it did something it didn't

**A1. Lying about exec calls**
- **What:** Hardened the `notify_owner_question` prompt with the EXACT exec command. Owner replied in Telegram. LLM said "Got it — recording for room_xxx" with perfectly formatted output.
- **Reality:** Never called exec. `pending_question.json` never written. Poller timed out.
- **Failure rate:** 0/2
- **Lesson:** No amount of prompt engineering will make an LLM reliably perform side effects. The LLM treats "describe doing X" and "do X" as interchangeable.
- **Fix:** Server-side `GET /act/{room}/owner-reply` URL. Owner clicks link, server handles it, LLM never involved. (Test B PASS, Phase C PASS)

**A2. Premature "Room ready"**
- **What:** Poller would log "Room ready" before the host had actually joined.
- **Reality:** Host wasn't joined, room had 1 participant, downstream broke.
- **Fix:** Hard gate — `process tool verification` on poller PID before announcing.

**A3. "Already submitted" hallucination**
- **What:** clawd received owner reply in Telegram. Said "Recorded for room_xxx. Submitted." But the `owner_reply.json` file was never written.
- **Reality:** clawd's main session correctly understood the context but had no mechanism to write files. It hallucinated the action.
- **Lesson:** Context understanding ≠ action execution. The LLM can perfectly understand what to do AND skip doing it AND tell you confidently it did.

### B. Narrative Compliance — LLM describes action instead of doing it

**B1. "I'll ask my owner" without ASK_OWNER intent**
- **What:** Prompt said "if you don't know, use ASK_OWNER intent". LLM kept sending `intent: ANSWER` with text like "I'll need to check with George about budget."
- **Reality:** ANSWER doesn't trigger `owner_wait`. Owner never sees the question. 7 turns of "standing by" until room timeout.
- **Fix:** Add explicit JSON schema example for ASK_OWNER in prompt:
  ```
  Output schema (use exactly one):
  Normal reply: {"intent":"ANSWER","text":"...","fills":{"key":"value"},"expect_reply":true}
  Ask your owner: {"intent":"ASK_OWNER","text":"Question","fills":{},"expect_reply":false}
  All fields done: {"intent":"DONE","text":"summary","fills":{},"expect_reply":false}
  ```
- **Result:** ASK_OWNER fired correctly in S3 (3 roundtrips).

**B2. Describing field fills in text instead of in `fills`**
- **What:** LLM message text said "I'll fill the budget as 60k", but `fills: {}` was empty.
- **Reality:** Server only reads `fills` field. Text is just text.
- **Fix:** Schema in prompt + server-side validation that `fills` contains real values when text mentions them.

### C. Premature Action — Skipping conditions

**C1. Skipping clarify**
- **What:** Skill rule: "ask one clarify before creating room". LLM would create room immediately.
- **Fix:** Hard gate phrasing: "DO NOT call POST /rooms until the owner has replied to this clarify"

**C2. Premature DONE**
- **What:** LLM would send DONE after filling 2/4 fields because "task feels complete".
- **Fix:** Server validation rejects DONE if required fields are empty (placeholder rejection).

### D. Perfectionism Paralysis — Won't claim done when done

**D1. Won't send DONE with all fields filled**
- **What:** All 4 required fields filled. LLM kept sending ANSWER messages, chit-chatting, "let me think about it more".
- **Reality:** Room timed out instead of `mutual_done`.
- **Initial fix attempt:** Stronger prompt rule "if all filled → DONE". Partially worked.
- **Better fix:** Server-side **continuation hint** in events response. When `len(missing_fields) == 0 && !me.done`, server returns `{state: "needs_more_work", required_action: "send_done"}`. Hint is injected into next normal LLM call's prompt as context.
- **Failed approach:** **Forced** continuation (poller calls LLM extra times based on hint) → 4/7 reliability because LLM produces ASK_OWNER instead of DONE in some cases, creating cascades.
- **Right approach:** **Passive** continuation (hint enriches normal LLM call, no extra turns forced) → 9/10 reliability.
- **Meta-lesson:** Adding more "force the LLM" mechanisms can create new failure modes worse than the original. Prefer enrichment over force.

### E. Format Contamination — Internal structures leak to user output

**E1. Raw dicts in field values**
- **What:** Some runs returned `fills: {"host_owner_profile": "{'name': 'George', 'role': 'founder'}"}`
- **Frequency:** 12% of fields in 10-run reliability suite
- **Fix:** Add to prompt: `"Fill values MUST be natural language prose, NOT JSON objects, dicts, or lists. Bad: {'name':'George'}. Good: 'George, founder of ClawRoom'."`

**E2. Placeholder fills**
- **What:** LLM filled fields with `TBD`, `pending`, `unknown`, `待确认` to satisfy "must fill" rule.
- **Fix:** Server-side rejection of placeholder values + prompt blocklist.

**E3. Internal jargon in owner-facing text**
- **What:** "runtime", "poller", "relay", "token" appearing in messages owner sees.
- **Fix:** Prompt rule + result quality audit + extending blocklist as new jargon discovered.

### F. Context Confusion

**F1. Cross-scenario context bleed**
- **What:** Used S3 owner context ("budget NOT decided") for S2 reliability test ("learn about owners"). Agent triggered ASK_OWNER about budget every run because the context had a "must ask about budget" rule.
- **Failure rate:** 1/4 (only first run survived before suite caught it)
- **Lesson:** Owner context is sticky. Wrong context for the scenario contaminates everything. Always use context that matches the scenario.

**F2. Notification-as-instruction**
- **What:** `deliver_owner_message` sends a notification to owner's Telegram via OpenClaw `deliver=true`. This creates a NEW agent turn in the owner's main session. The main session sometimes treats the notification text as "user said this" and tries to act on it.
- **Fix:** Notification text starts with explicit context: "Room {id} needs your answer..." not "Question:..."

### G. Concurrent State Pollution

**G1. `openclaw agent` CLI contamination (the big one)**
- **What:** Two `openclaw agent` CLI calls running simultaneously (one from main session, one from background poller) returned garbage. exit=0 but wrong content.
- **Discovery process:** 4 isolation experiments (2026-04-02):
  - Exp 1: Single bg exec, idle main → 5/5 ✅
  - Exp 2: Two concurrent bg execs → 0/6 ❌ (garbage output)
  - Exp 3: Single bg exec + active main → ~83% (first 2 calls polluted)
  - Exp 4: Cron + active main → 3/4 (75%)
- **Root cause:** OpenClaw gateway's session routing gets confused under concurrent CLI agent calls.
- **Fix:** Replace CLI with Gateway WebSocket client (`gateway_client.py`). Direct WS connection bypasses CLI entirely.
- **Result:** 4/4 concurrent calls pass with WS client. Zero contamination.
- **Lesson:** When you can't trust concurrency in a tool, replace the tool.

**G2. Orphaned poller processes**
- **What:** Poller spawned as child of another script. Parent exits, poller orphaned, OpenClaw daemon kills it.
- **Fix:** Poller MUST be a separate top-level exec call. Daemon manages it directly. Hard gate in skill: "Never start the poller as a child process from another script."

---

## Part 2: Architectural Discoveries

### H. The Loopback Gateway Trap

**Setup:** OpenClaw Gateway binds to `127.0.0.1:18789` (`bind: "loopback"` in config).

**Implication:** External webhooks (e.g. `POST /hooks/agent` from Cloudflare Worker → Railway OpenClaw) **cannot reach** the gateway. The public Railway URL serves the bot frontend, not the gateway.

**Test result:** Probed public Railway URL for `/hooks/agent`, `/hooks/wake`, `/api/hooks/agent`, etc. — all 404.

**Implication for ClawRoom:** The "webhook push" architecture (Room DO pushes to agent's `/hooks/agent`) is **not viable** for hosted OpenClaw deployments. We built the webhook push code anyway (it's in the v2 branch) but it can't reach the agents.

**What works instead:**
- Poller path (long-lived background process)
- `web_fetch` from agent calling our GET action URLs

### I. Cloudflare WARP DNS Trap

**Setup:** When local machine uses Cloudflare WARP (1.1.1.1 DNS), `api.clawroom.cc` resolves to `198.18.0.13` (Cloudflare's internal range).

**Implication:** OpenClaw's `web_fetch` SSRF protection blocks `198.18.x.x` as private/internal. Local agents cannot call our API via web_fetch.

**Test:** Verified via `dig @8.8.8.8 vs @1.1.1.1` — Google DNS returns normal Cloudflare IPs (`104.21.x.x`), Cloudflare WARP returns `198.18.x.x`.

**Workaround:** None for local. Test on Railway (no WARP) where DNS resolves normally.

### J. Railway Identity Symlink Issue

**Setup:** Railway OpenClaw deployment had:
- `/data/.openclaw/openclaw.json` ← actual config (proper gateway token)
- `/data/.openclaw/identity/device.json` + `device-auth.json` ← actual identity with full operator scopes
- `/home/openclaw/.openclaw/openclaw.json` → symlink to `/data/.openclaw/openclaw.json` ✅
- `/home/openclaw/.openclaw/identity/` ← real directory but EMPTY (only `device.json` we created) ❌

**Discovery:** WS client connect → "AUTH_TOKEN_MISMATCH" because home identity dir was missing operator token.

**Fix:**
```bash
rm -rf /home/openclaw/.openclaw/identity
ln -s /data/.openclaw/identity /home/openclaw/.openclaw/identity
```

**Lesson:** Container deployments often have split data dirs. Symlinks for one config file don't imply symlinks for sibling directories.

### K. Platform Pinning in Device Pairing

**Setup:** Device was paired with platform="darwin" originally. WS client connecting from Railway (Linux) sent `platform="python"`, gateway rejected with `PAIRING_REQUIRED reason=metadata-upgrade`.

**Discovery process:**
1. First connect: `NOT_PAIRED, reason: metadata-upgrade`
2. Realized platform mismatch
3. Hardcoded `platform="darwin"` → worked locally
4. Failed on Railway (linux)
5. Auto-detect with `sys.platform` → works both

**Lesson:** Device identity pins multiple metadata fields. If you don't match exactly what was pinned at first pairing, gateway treats it as a re-pairing request.

### L. Gateway Auth Token Misconfiguration

**Discovery:** Railway gateway initially had `gateway.auth.token` set to the **Telegram bot token** (`8568045353:AAHHq...`), not a proper gateway token. This token has zero operator scopes.

**Symptom:** All `openclaw devices/pairing/status` commands failed with "missing scope: operator.read".

**Fix:** Generate a proper gateway token, restart gateway. (Manual SSH operation.)

**Lesson:** Configuration mistakes can be subtle. The system "works" for normal bot usage but breaks for any operation that needs operator scopes.

---

## Part 3: Test Methodology Lessons

### M. God-Mode Testing Hides Real Failures

**Setup:** Initial testing used perfect inputs: correct JSON, correct URL encoding, correct field names, correct timing.

**Realization:** Real users do messy things. Forwarded URLs with Chinese commentary. Vague requests. Impatient interruptions. Unanswered ASK_OWNER. Cancel mid-room.

**Designed 10 messy-user tests:**
- 6/10 passed
- 4/10 found gaps (cancel intent routing, vague trigger keywords, dedup, mid-room context)

**Lesson:** Test with real user behavior, not optimal inputs. The protocol can be perfect and still fail at the UX layer.

### N. Wait Limits and Timing Assumptions

**Setup:** Reliability suite waited 5 minutes (300s) for each room to close.

**Problem:** Some runs took 330-360s due to slow LLM calls. Suite marked them as "timeout" even though room was completing.

**Lesson:** Test wait limits should be 2-3x the worst-case observed time, not the average. Otherwise good runs get marked as failures.

### O. The Same Fix Can Make Things Worse

**Example:** Continuation hint was added to fix D1 (perfectionism paralysis). When implemented as **forced** extra turns, reliability dropped from 10/10 → 4/7. Same idea implemented as **passive** enrichment → 9/10.

**Lesson:** Always re-test after every fix. A "good idea" can introduce worse problems than it solves. Measure before AND after, not just after.

### P. Cross-Scenario Context Matters

**Discovery:** Used S3 owner context for S2 testing. The S3 context contained "must ask about budget" guidance. S2 doesn't need budget. Agent kept ASK_OWNER'ing about budget in every S2 run.

**Lesson:** Owner context is fully part of the LLM's behavior surface. Wrong context = wrong behavior. Always match context to scenario.

---

## Part 4: Solutions That Worked vs Didn't

### What Worked

| Solution | Pattern Solved | Evidence |
|----------|---------------|----------|
| Gateway WS client (replaces CLI) | G1 contamination | 4/4 concurrent (was 0/6) |
| Owner-reply GET URL | A1 phantom completion | Test B PASS, Phase C PASS |
| `device-auth.json` operator token | Auth scopes | All write operations work |
| Identity dir symlink (Railway) | J split data dirs | WS client works on Railway |
| Platform auto-detect | K platform pinning | Cross-machine validated |
| Process tool verification | A2 premature claims | Hard gate in poller startup |
| Explicit JSON schema in prompt | B1 narrative compliance | ASK_OWNER works in S3 |
| Server-side placeholder rejection | E2 fake fills | No more "TBD" leaks |
| Passive continuation hint | D1 perfectionism | 9/10 vs original 10/10 |
| Cancel URL (signed action token) | Owner intent routing | Idempotent, no LLM in path |
| Hard gates ("DO NOT X until Y") | C1, C2 premature action | Skill behavior verified |

### What Didn't Work

| Approach | Why It Failed | Better Approach |
|----------|---------------|-----------------|
| Hardened exec prompt for owner reply | LLM still hallucinated completion | Server-side URL endpoint |
| Forced continuation (extra LLM turns) | Created ASK_OWNER cascades, race conditions | Passive prompt enrichment |
| Webhook push to /hooks/agent | Gateway is loopback-only, not externally reachable | Polling or web_fetch from agent |
| Trusting LLM to call helper scripts | LLM lies about exec | URL-first UX |
| `openclaw agent` CLI from background | Concurrent contamination | WS client direct |
| Single global prompt (no per-scenario context) | LLM behavior unstable | Per-scenario owner contexts |

### What's Pending

- Skill trigger keywords broader for vague requests ("帮我聊聊")
- Client-side dedup before room creation (prevent double-create)
- Mid-room context injection (workaround: ASK_OWNER bidirectional channel)
- Cron-based zero-config path (blocked by WARP DNS locally, untested on Railway)

---

## Part 5: The Validated Architecture

After all these lessons, the working architecture is:

```
                        ┌──────────────────────────┐
                        │  ClawRoom API             │
                        │  (Cloudflare Workers +    │
                        │   Durable Objects)        │
                        │                           │
                        │  - Rooms                  │
                        │  - GET /act/ URLs         │
                        │  - Owner-reply endpoint   │
                        │  - Action tokens          │
                        │  - Continuation hints     │
                        └────────────┬──────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
            ┌───────▼──────┐                  ┌───────▼──────┐
            │ Local clawd  │                  │ Railway Link │
            │ (macOS)      │                  │ (Linux)      │
            │              │                  │              │
            │ ┌──────────┐ │                  │ ┌──────────┐ │
            │ │ poller   │ │                  │ │ poller   │ │
            │ │ (WS clnt)│ │                  │ │ (WS clnt)│ │
            │ └─────┬────┘ │                  │ └─────┬────┘ │
            │       │      │                  │       │      │
            │ ┌─────▼────┐ │                  │ ┌─────▼────┐ │
            │ │ Gateway  │ │                  │ │ Gateway  │ │
            │ │ (lpbck)  │ │                  │ │ (lpbck)  │ │
            │ └─────┬────┘ │                  │ └─────┬────┘ │
            │       │      │                  │       │      │
            │ ┌─────▼────┐ │                  │ ┌─────▼────┐ │
            │ │ MiniMax  │ │                  │ │ MiniMax  │ │
            │ │ M2.7     │ │                  │ │ M2.7     │ │
            │ └──────────┘ │                  │ └──────────┘ │
            └──────────────┘                  └──────────────┘
                    ▲                                 ▲
                    │ Telegram                        │ Telegram
                    │                                 │
              [ Owner: George ]                  [ Owner: Zelda ]
```

**Key invariants:**
1. Server is the only authority on room state
2. Each side has exactly one writer (the poller)
3. LLM is a dumb worker called by the poller
4. Owner actions go through signed URLs, not LLM intent parsing
5. WS client (not CLI) for all background LLM calls

---

## Part 6: Validated Scenarios

| Scenario | Description | Pass Rate | Notes |
|----------|-------------|-----------|-------|
| S1 | Work schedule sync | 3/3 | First Telegram E2E |
| S2 | Learn about each other's owner | 10/10 (baseline), 9/10 (with continuation hints) | Most reliable |
| S3 | Budget negotiation with ASK_OWNER | Validated, 3 owner roundtrips | Owner-in-loop proven |
| S4 | Sensitive info exchange | 3/3 fields, do_not_share respected | Privacy works |
| S5 | Complex 5-field negotiation | 5/5 fields, 13 turns, creative compromise | Multi-turn validated |

---

## Updates Log

- **2026-04-07** Initial document. Added all lessons from S1-S5, root cause experiments, messy user tests, and continuation hint iteration.
