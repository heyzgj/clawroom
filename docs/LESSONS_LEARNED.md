# ClawRoom Lessons Learned

A running document of every experiment, every pitfall, every "we thought X would work but Y happened" moment from building ClawRoom. The goal: never re-learn the same lesson twice.

Last updated: 2026-04-14

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

## Part 6: v3 Relay Experiments (2026-04-13)

These lessons come from rebuilding ClawRoom from scratch with a 50-line Cloudflare Worker relay (GET-only, KV-backed, token-in-querystring) and testing agent-native polling via OpenClaw `web_fetch` + `cron.add`.

### Q. Skill Keyword Hijacking in Isolated Sessions

**What:** Cron messages that mentioned "ClawRoom" by name caused isolated cron sessions to load the v2 ClawRoom skill. The skill overrode verbatim web_fetch instructions and routed calls to `api.clawroom.cc` instead of the v3 relay.

**Symptom:** "room not found" / "ClawRoom Join Failed" in cron reports — even though the relay thread existed and was accepting messages via direct curl.

**Root cause:** v2 SKILL.md trigger: *"Any mention of 'ClawRoom' by name"*. Isolated sessions have the skill loaded. The keyword fires the skill. The skill wins.

**Fix:** Never use the product name in cron message text. Use neutral language: "relay thread", "coordination session", "polling job". This applies to any text that will run in an isolated session.

**Lesson:** Skill trigger keywords are global across isolated sessions. A cron message is not a neutral prompt — it runs in a context where every installed skill is available and looking for its trigger.

### R. cron.add ≠ Background Process (OpenClaw-Specific)

**What:** OpenClaw has two distinct "cron" mechanisms:
1. Real `cron.add` (Gateway-backed, persists across session restarts, proper tool call)
2. Fake "cron" (background PID, jobs.json, subprocess — dies when session ends)

When the Gateway is in "service disabled" state, `cron.add` fails. Bots do NOT error — they silently fall back to fake cron (writing to jobs.json, spawning background PIDs). The bot reports success. The cron dies with the session.

**Discovery:** Gateway PID was running but CLI showed "Gateway service disabled." All previous "cron.add" confirmations in testing were background processes. The first `cron.add` that actually worked was Clawd's (different runtime, working Gateway).

**Prior state in docs:** "Cron-based zero-config path blocked by WARP DNS locally, untested on Railway." Correction: WARP DNS is not the issue on Railway. The blocker is Gateway "service disabled" state. Two separate problems.

**Verification step:** After any `cron.add`, require the bot to say the exact job name it created. Background processes cannot self-identify as cron jobs by name. If the bot lists a PID or "jobs.json entry", the cron.add failed.

### S. Duplicate cron.add from Confirm Step

**What:** Asking the bot to "confirm cron.add succeeded" after the initial setup sometimes triggers a *second* `cron.add` call. Two instances with the same name then fire simultaneously.

**Symptom:** Thread received 5 identical guest opening messages within ~5 minutes. Both cron instances read `guest_count=0` simultaneously and both wrote an opening before either write had propagated.

**Fix (two layers):**
1. Instruction: "Call cron.add exactly once. Do NOT call it again to confirm. Report the job name that was created." Remove any confirm/verify step from the instruction.
2. Relay-side rate limit: same role cannot post twice within 10 seconds (returns HTTP 429). This catches races even if the instruction fix fails.

**Lesson:** The "confirm" pattern that works in human interaction is dangerous with LLMs performing side-effectful tool calls. Every "verify X" instruction can silently become "do X again."

### T. Relay Reliability Baseline

**The 50-line GET-only relay is sound.** Across all experiments, every relay failure was traced back to agent-side issues. Direct curl tests succeeded instantly in every case.

**What was proven:**
- `GET /threads/new?topic=...` → create: works
- `GET /threads/:id/post?token=...&text=...` → send: works  
- `GET /threads/:id/msgs?token=...&after=N` → poll: works
- `GET /threads/:id/done?token=...` → close: works
- Token-in-querystring: works for web_fetch (no Authorization header needed)
- KV propagation: consistent within seconds, not the source of "not found" errors

**When you see "room not found" from an agent:** The relay has the room. Check for (a) skill keyword collision (Q above), (b) wrong endpoint being called (v2 URL vs v3 URL), (c) agent hallucinating the error.

### U. v3 Confirms Meta-Lesson: LLM as Executor Fails

The v3 rebuild independently re-derived the v2 meta-lesson through a different path.

**v2 path:** Hardened exec prompts → LLM still hallucinated completion (A1) → Fix: Python poller as executor, LLM as decision maker only.

**v3 path:** web_fetch + cron in isolated sessions → URL encoding unreliable, error handling unreliable, success reports unverifiable → LLM reports "sent" without relay receiving message.

**Same conclusion:** The LLM is a reliable *decision maker* (what to say, when to close, how to respond). The LLM is an unreliable *executor* (formatting URLs, handling HTTP errors, confirming side effects).

**The architectural invariant holds:** Code executes. LLM decides. Whether "code" is a Python poller (v2) or a verified-execution relay driver (v3) is an implementation detail — the separation is fundamental.

### V. Cron Timing Is Non-Deterministic Beyond Early Fires

**What:** In Experiment C2 (isolated cron, 60s interval), fire 1 and fire 2 executed at expected ~62s intervals. Fire 3 was delayed by **~16 minutes** before finally executing.

**Observed timestamps:**
- Fire 1: 18:40:52 (ping 1)
- Fire 2: 18:41:55 (+63s, ping 2)
- Fire 3: 18:57:53 (+957s = ~16 min, ping 3)

**What we can and cannot conclude:**
- The cron job did NOT stop — fire 3 eventually executed correctly (ping 3 appeared on relay).
- Manual execution during the delay window worked instantly, confirming the instructions and relay were fine.
- Whether fires 3–N phantom-completed between 18:42 and 18:57 (Lesson U pattern), or the Gateway simply delayed scheduling, is unknown without internal logs.
- Both explanations produce the same observable behavior: from the outside, the cron "appears stuck" for ~15 minutes before resuming.

**Lesson:** A 60s cron interval does not mean 60s response time. For any time-sensitive agent coordination (e.g., waiting for the other agent to reply), cron timing variance can be 10–15x the nominal interval. Do not design protocols that assume timely cron execution.

**Implication for Experiment D (two-bot exchange):** If Bot A waits for Bot B's cron to reply, the wait could be 16 minutes even when everything is "working." This makes cron-only coordination unsuitable for interactive exchanges. Consider: main-session polling in a loop as the alternative (no cron, just repeated web_fetch calls until done).

### W. cron.add Is Not Available in All OpenClaw Deployments

**What:** Experiment D revealed that Link's OpenClaw instance does not have `cron.add` in `plugins.allow`. The tool call simply failed with "not available in current config."

**Implication:** The product's autonomy claim — "any agent can participate just by receiving the invite URL" — currently requires `cron.add` to be available. Without it, the receiving agent can only participate manually (main session, owner must stay present).

**Deployment matrix:**
- Both sides have cron.add → fully autonomous ✅
- Only one side has cron.add → one-sided autonomy ⚠️
- Neither side has cron.add → fully manual ❌

**Fix direction:** Either (a) require cron.add as a stated dependency in the skill install instructions, or (b) design a fallback that works without cron (main-session polling loop that completes within one session lifetime).

### X. Isolated Cron "Wait" Instructions Are Overridden by LLM Helpfulness

**What:** In Experiment D, Clawd's host cron was instructed: "if only host messages and no guest reply → stop, wait." Instead, Clawd sent 15 host messages over ~15 minutes — a new variation every fire.

**Observed behavior:** Each isolated session saw the existing host messages, recognized there was no guest reply, and then sent a new message anyway — with varied phrasing, escalating urgency, and eventually hallucinated responses (id=9: host sent "Wednesday 2pm works for me" as if agreeing to a guest reply that never existed).

**Root cause:** The LLM's default behavior when "the task isn't done yet" is to try harder. "Stop, wait" is a passive instruction that conflicts with the LLM's helpfulness drive. In an isolated session with no memory of previous fires, each fire independently concludes "the problem isn't solved, I should act." The instruction is re-evaluated fresh every time and loses to the LLM's judgment.

**This is a new variant of Lesson U:** Not phantom completion (claiming to act without acting) but phantom conversation (acting without authorization, generating content the other party never said).

**Fix:** Never rely on LLM-side "wait" logic in isolated cron. The relay must enforce turn-taking at the server level (see Lesson Y). The LLM cannot be trusted to hold itself back.

### Y. Relay KV Has Race Condition Under Concurrent Multi-Bot Writes

**What:** Link successfully posted (relay returned HTTP 201 with a message ID), but the message never appeared in subsequent reads. Clawd's concurrent host cron write overwrote Link's guest write.

**Root cause:** The relay's write path is a non-atomic read-modify-write on KV:
```
msgs = KV.get(key)       // read
msgs.push(newMsg)        // modify  
KV.put(key, msgs)        // write (overwrites anything written since the read)
```
Cloudflare KV is last-write-wins with no compare-and-swap. Two workers executing this sequence simultaneously will have the second writer silently discard the first writer's changes.

**Observed impact:** Guest message received HTTP 201 and a valid message ID, but was permanently lost. From the guest's perspective the write succeeded. From the oracle's perspective it never happened.

**Fix:** Use Cloudflare Durable Objects (single-threaded by design) for the message store instead of KV. Or serialize writes with a D.O. mutex. KV is appropriate for read-heavy workloads, not concurrent writes from independent workers.

**Secondary fix:** Add relay-side turn-taking enforcement: reject a message from role X if the last message in the thread is already from role X. This eliminates the spam problem (Lesson X) and reduces concurrent write frequency simultaneously.

---

## Part 7: v3.1 DO Relay + Verified Bridge + Real Telegram E2E (2026-04-14)

This section syncs the full path from the proposed ClawRoom v3 plan to the first real local-plus-Railway Telegram E2E pass.

### Starting Proposal

The original v3 proposal was:

1. A Cloudflare Worker relay with GET-friendly room APIs.
2. A zero-npm Node `bridge.mjs` that talks to the relay and to the local OpenClaw Gateway at `ws://localhost:18789`.
3. An OpenClaw skill that downloads/checks the bridge, starts it in the background, then returns immediately.
4. Direct Telegram Bot API notification at close, instead of trying to deliver through an active OpenClaw session.

The core intuition was right: use code for transport and side effects, and use OpenClaw only for deciding what to say. The part that needed correction was the storage/runtime layer: "pure KV relay" is not product-grade for concurrent agent writes, and `nohup ... &` is not enough proof that a bridge is alive.

### Design Correction

The stable v3.1 shape is:

1. Durable Object room core, not KV. Cloudflare's own docs position Durable Objects for coordination, while KV docs say KV is not ideal for atomic read-modify-write workloads: <https://developers.cloudflare.com/durable-objects/> and <https://developers.cloudflare.com/kv/concepts/how-kv-works/>.
2. Relay API remains GET-friendly for OpenClaw compatibility, but writes go through a single DO instance per room.
3. Server-side turn gate: if the last relay event is already from the same role, reject the write with `409`.
4. Explicit idempotency key on bridge writes.
5. Dedicated `clawroom-relay` OpenClaw agent, not `main`.
6. Session key isolated by room and role: `agent:clawroom-relay:clawroom:<thread>:<role>`.
7. Verified launcher with runtime-state, heartbeat, PID, log path, and relay heartbeat proof.
8. Direct Telegram Bot API notification with explicit owner/chat binding.

This turned the v3 plan from "a clever relay spike" into a runtime-hardening plan.

### What Was Implemented in `/Users/supergeorge/Desktop/project/clawroom-v3`

1. `relay/worker.ts`: SQLite-backed Durable Object relay with create, join, long-poll messages, post, close, heartbeat, turn gate, and mutual-close state.
2. `relay/wrangler.toml`: `THREADS` Durable Object binding and SQLite migration.
3. `bridge.mjs`: zero-npm Node bridge that connects relay <-> OpenClaw Gateway, uses `clawroom-relay`, scans every line for `REPLY:` / `CLAWROOM_CLOSE:`, persists cursor/runtime state, heartbeats, and calls Telegram Bot API on close.
4. `launcher.mjs`: verified detached launcher that starts the bridge, waits for runtime-state + relay heartbeat, and fails clearly instead of pretending background launch succeeded.
5. `SKILL.md`: OpenClaw-facing launch instructions.
6. `docs/REAL_TELEGRAM_E2E.md`: real cross-machine runbook.
7. `scripts/telegram_e2e.mjs`: harness that creates a thread, sends both Telegram prompts through Telegram Desktop, and monitors relay closure.
8. `scripts/validate_e2e_artifact.mjs`: validator for closed state, mutual close, turn-taking, runtime stopped, summary present, and echo-loop avoidance.
9. Railway repair helpers for this deployment: `fix_railway_clawroom_agent.mjs`, `inspect_notify_config.mjs`, `set_telegram_allow_from_from_sessions.mjs`.

### Actual E2E Path

The passing product-path test was:

1. Local harness created a DO relay thread on `https://clawroom-v3-relay.heyzgj.workers.dev`.
2. Harness sent a host launch prompt to local clawd's Telegram bot.
3. Local OpenClaw downloaded/used the v3.1 launcher and started the host bridge in the local runtime.
4. Harness sent a guest launch prompt to Railway-hosted Link's Telegram bot.
5. Railway Link OpenClaw downloaded/used the same launcher and started the guest bridge inside the Railway container.
6. Host bridge asked OpenClaw for the opening message and posted to relay.
7. Guest bridge long-polled relay, sent the host message to Railway OpenClaw Gateway, posted the guest reply.
8. Host bridge observed the guest reply, asked local OpenClaw for the final close, posted close, and notified owner.
9. Guest bridge observed host close, posted guest close, and notified owner.
10. Relay marked `closed: true` only after both sides closed.

Passing room:

```json
{
  "room_id": "t_92615621-4a8",
  "stop_reason": "mutual_close",
  "turn_count": 4,
  "message_count": 2,
  "close_count": 2,
  "roles": "host -> guest -> host -> guest"
}
```

Artifact:

`/Users/supergeorge/.clawroom-v3/e2e/t_92615621-4a8.json`

Validator passed:

1. Room closed.
2. Host and guest both sent close.
3. Four relay events existed.
4. Two negotiation messages existed before close.
5. Roles alternated with no same-role spam.
6. Host and guest runtime heartbeats ended as `stopped`.
7. Summary was present.
8. Transcript was not an echo loop.

### Z. `railway run` Is Not a Remote Runtime Test

**What:** Early "cross-machine" attempts used `railway run node bridge.mjs`. That was not a real remote execution test.

**Source check:** Railway docs and CLI help say `railway run <COMMAND>` runs a local command with Railway variables injected. Railway `ssh` opens a shell inside a deployed service container. See <https://docs.railway.com/cli/run> and <https://docs.railway.com/cli/ssh>.

**Impact:** `railway run` can prove env-variable shape, but it cannot prove that the Railway-hosted OpenClaw Gateway was contacted. It will call the local machine's gateway if the command runs locally.

**Fix:** For diagnostics, use `railway ssh` or dashboard SSH to inspect the actual container. For product E2E, do not SSH to start the bridge; send a Telegram prompt to the Railway-hosted OpenClaw and verify its own runtime creates the guest heartbeat/log.

### AA. SSH Is Diagnostic, Not Product Path

**What:** SSH was useful to find container-specific problems: Node version, native WebSocket support, gateway reachability, state dir, identity files, agent workspace, and notification config.

**Boundary:** A test only becomes product-path E2E when the guest bridge is started by the Railway-hosted OpenClaw after receiving a Telegram prompt. Operator SSH is allowed for preflight and repair; it is not allowed as the launch mechanism in a passing product test.

**Pass proof:** Room `t_92615621-4a8` passed because the guest prompt went to `@link_clawd_bot`, and the resulting runtime files/logs appeared under Railway's OpenClaw state dir.

### AB. Respect `OPENCLAW_STATE_DIR`; Railway Is Not `$HOME/.openclaw`

**What:** Railway's Link deployment stores OpenClaw state in `/data/.openclaw`, while `$HOME` is `/root`. The initial bridge/launcher assumptions looked in `/root/.openclaw`.

**Symptom:** Gateway identity/config lookup and `clawroom-relay` workspace paths pointed at `/root/...`; guest bridge failed with permission errors while trying to create `/root/.openclaw/workspaces/clawroom-relay`.

**Fix:** Bridge and launcher must use `OPENCLAW_STATE_DIR` when present, and default runtime state/logs under `${OPENCLAW_STATE_DIR}/clawroom-v3` on Railway. Agent workspace for `clawroom-relay` must be writable on the persistent volume: `/data/.openclaw/workspaces/clawroom-relay`.

### AC. Gateway Client Schema Is Strict

**What:** The first host bridge connected to the OpenClaw Gateway but failed the connect schema with `client.id` mismatch.

**Symptom:** Gateway rejected the bridge with an invalid connect params error.

**Fix:** Use the expected Gateway client id (`gateway-client`) and keep a local smoke test before attempting Telegram E2E. A bridge that cannot pass the Gateway handshake is not ready to be launched by Telegram.

### AD. Dedicated Agent Isolation Is Required

**What:** Running ClawRoom bridge calls through `main` risks session lock contention and main-session contamination.

**Fix:** Use a dedicated `clawroom-relay` agent and isolate session keys by room and role:

```text
agent:clawroom-relay:clawroom:<thread>:<role>
```

**Runtime requirement:** The dedicated agent must exist and its workspace must be writable in each runtime. Do not treat "agent name exists in config" as sufficient; verify the actual workspace path.

### AE. Direct Telegram Notification Needs Owner Binding

**What:** Direct Bot API notification worked locally but initially skipped on Railway because the guest runtime did not have a usable chat target.

**Source check:** Telegram Bot API `sendMessage` requires both `chat_id` and `text`: <https://core.telegram.org/bots/api#sendmessage>.

**Fix:** Notification config must include:

1. bot token source (`TG_BOT_TOKEN`, `TELEGRAM_BOT_TOKEN`, or OpenClaw config),
2. owner chat id / allowFrom binding,
3. redacted logs,
4. idempotent send behavior,
5. clear skip/fail status when notify target is missing.

**Do not use:** OpenClaw `deliver` as the primary close notification path. It depends on active session context and can create notification-as-instruction confusion.

### AF. A Verified Launcher Beats `nohup ... &`

**What:** A detached bridge can fail after the parent session returns. A plain `nohup node bridge.mjs &` tells us only that the shell accepted a command.

**Fix:** The launcher must wait for:

1. child PID alive,
2. runtime-state file created,
3. relay heartbeat visible,
4. log path written,
5. failure path surfaced to the caller.

**Product implication:** The OpenClaw skill can return quickly, but it must return a verified launch JSON, not a hopeful "started in background" message.

### AG. The E2E Oracle Must Be the Relay + Runtime State, Not Telegram Vibes

**What:** Telegram messages can make a run feel successful while the relay lost a write, one side never closed, or a notification skipped.

**Fix:** The validator must check machine facts:

1. `closed: true`,
2. `host_closed` and `guest_closed`,
3. relay event count,
4. host/guest close events,
5. turn alternation,
6. both runtime heartbeats stopped,
7. final summary present,
8. no trivial echo loop,
9. bridge logs show Telegram delivery without exposing secrets.

**Lesson:** The owner-facing UX is Telegram, but the release gate is state.

### AH. Asset Download Is an Integration Bridge, Not the Final Packaging

**What:** The passing E2E used a downloadable test bundle for `launcher.mjs` and `bridge.mjs`, which let both OpenClaw runtimes self-install at launch time.

**What this proves:** OpenClaw can install and run the bridge in its own environment without SSH as product path.

**What remains:** Production should replace the temporary asset URL with either a bundled skill asset or a signed/hash-pinned manifest. The download path is acceptable for E2E and beta hardening, not as the final trust model.

### AI. Marker-Scan Contracts Are a New LLM-Protocol Seam (preemptive)

**What:** v3.1's bridge detects agent intent by scanning OpenClaw output for the exact strings `REPLY:` and `CLAWROOM_CLOSE:`. If the LLM emits `Reply :`, `REPLY：` (fullwidth colon), `reply →`, `CLAWROOM_CLOSE ` (trailing space), or omits the marker entirely, the bridge silently misses the event.

**Why preemptive:** This is the same failure class as B1 (narrative compliance) and E1–E3 (format contamination), now relocated to the bridge ↔ OpenClaw seam. We have not observed it yet: the passing room `t_92615621-4a8` emitted clean markers. But that scenario was friendly — short prompts, one exchange, one close. Messier, multi-turn, bilingual, or ASK_OWNER scenarios will stress the contract.

**Mitigations to apply before the next multi-turn E2E:**

1. Replace exact-string match with a tolerant regex: `/^\s*(REPLY|CLAWROOM[_ ]CLOSE)\s*[:：]/i`. Handles leading whitespace, fullwidth colon, underscore-or-space, case.
2. Counter for unmatched turns. If a run produces non-empty agent output but zero matched markers for the whole turn, that is a silent drop — log it loudly and surface it in the validator.
3. Conservative fallback: if the entire turn ends with non-empty output and no marker at all, classify as `REPLY:` with the full text (fail-safe forward), but flag as `marker_inferred=true` so prompt drift is visible, not masked.
4. Keep marker strings, tolerant regex, and fallback policy in one canonical location in `bridge.mjs` so skill/prompt updates always reference the same contract.

**Lesson (general form):** Every place where an LLM's free-text output becomes a side-effect trigger is a narrative-compliance seam. Guard it with tolerant parsing on the reader side and telemetry on the unmatched case. Prompt rules alone are not enough — they fail silently.

---

## Updates Log

- **2026-04-07** Initial document. Added all lessons from S1-S5, root cause experiments, messy user tests, and continuation hint iteration.
- **2026-04-08** Hardcore E2E re-run on the public `npx skills add heyzgj/clawroom` install path. Three rounds with subagent pairs as fresh OpenClaw installs. Drove the four real fixes that became skill v2.2.0 → v2.2.1 (open-immediately, status-shape example, fills-every-send, GET-only API surface). All rounds passed after fixes.
- **2026-04-13** v3 relay experiments. Added Q (skill keyword hijacking), R (cron.add vs background process), S (duplicate cron.add from confirm step), T (relay reliability baseline), U (v3 confirmation of LLM-as-executor failure). Updated pending item: cron path is NOT blocked by WARP DNS — blocked by Gateway service disabled state.
- **2026-04-13** C2 results. Added V (cron timing non-deterministic beyond early fires — 16min delay observed on fire 3 despite 60s nominal interval). B experiment PASSED 3/3 (main session web_fetch reliable). C2 PARTIAL PASS (all 3 pings arrived but fire 3 delayed ~16min).
- **2026-04-14** v3.1 hardening and real Telegram E2E. Added Part 7 (DO relay + verified bridge + Telegram self-launch path), lessons Z–AH, and the first passing local clawd plus Railway Link run (`t_92615621-4a8`, mutual close, 4 relay events, both owner notifications delivered). Redacted artifact co-located at [`docs/progress/v3_1_t_92615621-4a8.redacted.json`](progress/v3_1_t_92615621-4a8.redacted.json). Added Lesson AI (preemptive: REPLY:/CLAWROOM_CLOSE: marker-scan robustness) before the next multi-turn / ASK_OWNER E2E.
