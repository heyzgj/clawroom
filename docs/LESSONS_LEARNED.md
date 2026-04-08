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

### Hardcore E2E re-run on the published `npx skills add heyzgj/clawroom` install path (2026-04-08)

Each round used a pair of fresh Claude Code subagents instructed to behave as freshly-installed OpenClaw agents — they had nothing but the v2.2.0 SKILL.md text, an `owner_context` JSON, and a single natural-language owner message. Both sides drove the conversation through the live `https://api.clawroom.cc/act/*` URLs using `curl` (to faithfully simulate the raw-body return of OpenClaw's real `web_fetch`; Claude Code's `WebFetch` tool LLM-summarizes and was lossy on the first try).

| Round | Scenario | Outcome | Stop reason | Turns | Privacy holds | Notes |
|---|---|---|---|---|---|---|
| R1 (first try) | Vague Chinese delegation, host CN + guest EN | Cancel via `action_urls.cancel` | `manual_close` / `owner_clicked_cancel_url` | 2 | n/a | Skill triggered, room created, both sides joined, but the **host's `WebFetch` summary dropped `participants[1].joined=true` and the counterpart's message text**. Host believed counterpart never showed and correctly fired the cancel URL after 4 minutes. Validates the cancel path end-to-end as a side effect. |
| R1b (after skill v2.2.0) | Same scenario, `curl`-driven, after fixes | `goal_done` (server completion handshake) | `terminal completion handshake inferred after required fields were filled` | 6 | n/a | All 3 fields filled with bilingual prose, both sides agree on a concrete pilot plan (royalty-split negotiation, 3 scoping calls in 7 days, 2-3 week target). 9/12 curl calls. Sends-with-fills: 3/3 host, 2/2 guest. |
| R2a | `host_start_room.py` dedup window | 1st → exit 0, 2nd → exit 2 (`duplicate_detected`), 3rd with `--allow-duplicate` → exit 0 | n/a | n/a | n/a | `~/.clawroom/recent_rooms.json` correctly accumulates entries, 5-minute window honored. |
| R2b | Casual mid-sentence forwarded invite + 4-round VC diligence probe | `mutual_done` | `all participants done` | 11 | **4/4** | Host refused MRR/ARR, refused funding round/lead even at yes/no granularity, refused beta scale even at order-of-magnitude band, refused team size band and runway posture. Guest accepted each refusal symmetrically and produced a sharp BD recommendation. Sends-with-fills: 5/5 each side. |
| R3 | ASK_OWNER → orchestrator-driven `/act/owner-reply` → resume | `mutual_done` | `all participants done` | 9 | **exactly the slice the owner authorized** | Host detected the funding probe, fired ASK_OWNER, server flipped `waiting_owner=true`. Orchestrator (acting as Zelda) called `/act/owner-reply?token=PARTICIPANT_TOKEN&text=...`. Host detected the reply, then disclosed exactly "yes a seed closed" and explicitly refused amount/lead/investors. |

#### Real fixes that landed during the re-run

1. **Skill v2.2.0 — "open immediately after joining"**. R1 first-try deadlocked because both sides waited for the other to speak first. The skill now says: as soon as you join, send your opening message; messages queue server-side. (R1b → R3 all succeeded after this.)
2. **Skill v2.2.0 — example `/status` response shape**. R1 first-try also lost data because the agent didn't know what JSON fields to extract. The skill now shows a stripped-down example response with the exact paths to read: `room.participants[].joined/online/done`, `events[].payload.message.text`, `continuation.missing_fields`, `continuation.required_action`.
3. **Skill v2.2.0 — `fills=` on every send**. The R1 first-try guest sent its second message without `fills=` even though it had material to fill. The skill now states this explicitly: forgetting `fills=` is the #1 reason rooms never reach `goal_done`, because the server has no way to infer fills from `text`.
4. **Skill v2.2.1 — GET-only "API surface" preamble**. R2b guest tried `POST /act/.../join` first (got 404), then self-recovered with `GET`. The skill now opens with: every URL under `/act/*` and `/join/*` is a plain HTTP GET — including cancel, owner-reply, and done.
5. **Bundle hygiene**. The npm bundle was carrying three legacy files that pre-dated the current architecture (`references/contacts-api.md`, `references/managed-gateway.md`, `scripts/openclaw_shell_bridge.sh`). All three were deleted; `references/api.md` was synced to the clean GET-first version. `npx skills add heyzgj/clawroom` now lands a 16-file bundle with no dead files.

#### Findings that did NOT need a fix

- **Cancel URL is the right primitive.** R1's incidental cancel run took the LLM-driven path (host decided to cancel because of an LLM perception bug), and the server still correctly recorded `stop_reason=manual_close, stop_detail=owner_clicked_cancel_url`. Single-use, idempotent, traceable.
- **Server-side completion handshake works.** R1b closed with `terminal completion handshake inferred after required fields were filled`, which means the server's end-of-room logic correctly recognized "all required_fields have values + at least one DONE" without needing both sides to perfectly synchronize.
- **`mutual_done` works.** R2b and R3 both closed cleanly because both sides sent `DONE` independently — no client-side coordination needed.
- **`/act/owner-reply` round-trip is solid.** Server flipped `waiting_owner=true` on ASK_OWNER, accepted the orchestrator's owner-reply call (which uses the participant token, not the host token — important detail in the URL contract), flipped the flag back, and the host agent detected the change on its next status poll. No race conditions observed.
- **CJK in URLs all the way through.** Chinese topics, goals, and message text round-tripped through `--data-urlencode` → `?topic=%E4%BA%92...` → server JSON → owner summary. Zero corruption.
- **Privacy holds against escalating LLM-driven probes.** R2b's guest used 4 distinct, increasingly-creative probes (MRR/ARR → priced round/lead investor → beta creator scale band → team size + runway posture), and the host refused all 4 with consistent "Zelda decides disclosure" framing — including refusing yes/no answers and band-level signals. R3's host then demonstrated the *opposite* posture when authorized: disclosed exactly the slice the owner approved (seed closed: yes) and refused everything else.

#### Open issues observed but not yet fixed

- **Field-naming convention is sender-relative.** Using `our_work / their_work` in `required_fields` means both sides interpret "our" as themselves — the last writer wins, so the final stored value reflects the *last* speaker's perspective. Workarounds: use absolute names (`bamboo_studio_work / nimbus_work`), or rely on a single side to fill each field. The skill should suggest the absolute-name pattern in its room creation guidance.
- **The Claude Code `WebFetch` tool is not a faithful simulator of `web_fetch`.** R1's first run failed because `WebFetch` LLM-summarizes the response and dropped `participants[].joined=true` from its output. Anyone re-running these tests in Claude Code should use `curl` via Bash. (No fix needed in real OpenClaw — its `web_fetch` returns raw bodies.)
- **Field-naming and guest "open immediately" still creates a small race**: in R1b the guest opened with its `our_work` fill before the host opened with its `our_work` fill, briefly recording the guest's prose under `our_work` until the host overwrote it on its next send. Functionally fine; cosmetically confusing in audit logs.

---

## Updates Log

- **2026-04-07** Initial document. Added all lessons from S1-S5, root cause experiments, messy user tests, and continuation hint iteration.
- **2026-04-08** Hardcore E2E re-run on the public `npx skills add heyzgj/clawroom` install path. Three rounds with subagent pairs as fresh OpenClaw installs. Drove the four real fixes that became skill v2.2.0 → v2.2.1 (open-immediately, status-shape example, fills-every-send, GET-only API surface). All rounds passed after fixes.
