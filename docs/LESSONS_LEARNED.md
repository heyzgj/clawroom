# ClawRoom Lessons Learned

A running document of every experiment, every pitfall, every "we thought X would work but Y happened" moment from building ClawRoom. The goal: never re-learn the same lesson twice.

Last updated: 2026-04-15

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

### AI. Marker-Scan Contracts Are a New LLM-Protocol Seam

**What:** v3.1's bridge detects agent intent by scanning OpenClaw output for the exact strings `REPLY:` and `CLAWROOM_CLOSE:`. If the LLM emits `Reply :`, `REPLY：` (fullwidth colon), `reply →`, `CLAWROOM_CLOSE ` (trailing space), or omits the marker entirely, the bridge silently misses the event.

**Why it mattered:** This is the same failure class as B1 (narrative compliance) and E1-E3 (format contamination), now relocated to the bridge <> OpenClaw seam. The first passing smoke room `t_92615621-4a8` emitted clean markers, but that scenario was friendly: short prompts, one exchange, one close. Messier, multi-turn, bilingual, or ASK_OWNER scenarios stress the contract.

**Mitigations applied before the next multi-turn E2E:**

1. `bridge.mjs` now uses tolerant regexes for `REPLY` and `CLAWROOM[_ ]CLOSE`, including fullwidth colons and case differences.
2. It tracks `unmatched_marker_turns` and `last_marker_inferred_at` in state/runtime-state.
3. Conservative fallback classifies non-empty unmarked output as a reply, logs `marker inferred`, and keeps the room moving.
4. Marker strings, tolerant regex, and fallback policy live in one canonical parser in `bridge.mjs`.

**Lesson (general form):** Every place where an LLM's free-text output becomes a side-effect trigger is a narrative-compliance seam. Guard it with tolerant parsing on the reader side and telemetry on the unmatched case. Prompt rules alone are not enough — they fail silently.

### AJ. Minimum Conversation Length Needs a Code Gate

**What:** T2-full room `t_f8d18771-716` asked for at least 8 negotiation messages in the goal text, but both agents closed after 4 negotiation messages.

**Symptom:** Validator with `--min-messages 8` failed only `message_count`; transport, turn-taking, runtime stop, mutual close, and notification all worked.

**Fix:** Add bridge-level `--min-messages`, pass it through `scripts/telegram_e2e.mjs`, include current message count in prompts, and suppress early close attempts before the threshold.

**Result:** Follow-up room `t_0b3602a9-e3b` passed T2-full transport/runtime gates: 8 negotiation messages, 2 close events, mutual close, both runtimes stopped, no echo loop.

**Caveat:** This does not prove mandate enforcement. The host accepted `¥73k`, above the stated `¥65k` ceiling. T3/owner-in-the-loop remains the next required validation.

**Lesson:** Numeric protocol constraints belong in bridge code, not only in natural-language goals. Authorization constraints need their own gate, not just better wording.

### AK. Owner-Reply Tokens Must Never Ride Mutating GET URLs

**What:** First T3 room `t_1f72571a-3f4` correctly emitted `ASK_OWNER` after a `¥75,000` proposal exceeded the host's `¥65,000` mandate, but the Telegram notification contained a tokenized GET owner-reply URL with placeholder text.

**Symptom:** The relay recorded repeated `owner_reply` events whose text was `REPLACE_WITH_OWNER_DECISION`. This strongly indicates a link preview/unfurl or bot-side automation fetched the URL and consumed the single-use token before the owner/harness replied.

**Fix:** Make `/threads/:id/owner-reply` POST-only, remove mutating GET URLs from bridge notifications, add `owner_reply_content` validator checks, and make `waiting_owner` bridges observe closed rooms before polling owner replies.

**Result:** Follow-up room `t_fb3fda2d-563` passed T3 v0: `ASK_OWNER` -> concrete `owner_reply` -> host resumed at `¥65,000` -> mutual close within mandate -> both runtimes stopped.

**Lesson:** A one-time side-effect token in a chat surface must not be invokable by GET. Link previews are actors. Treat GET as read-only, especially around authorization.

### AL. ASK_OWNER Must Be a Human Reply UX, Not Just a Notification

**What:** Strict T3 v1 room `t_e5f0c995-23e` reached the real ASK_OWNER gate across local clawd + Railway Link: host and guest negotiated to `¥75,000`, host posted `ask_owner`, the bridge delivered Telegram message `1887`, and it wrote the `(chat_id, message_id) -> owner_reply_token` binding. No `owner_reply` with `source: telegram_inbound` reached the relay before the monitor timed out.

**What this proves:** The bridge/relay/runtime path can reach the owner authorization point on real cross-machine infrastructure. It does not prove the ordinary-owner reply path, because Codex cannot impersonate George's Telegram user, Telegram Desktop was not automatable in this environment, and Telegram Web was not logged in.

**Product failure:** The owner notification was still too developer-shaped. It described a reply path but did not make the 99% action obvious enough: "reply directly to this Telegram message." In an average-user E2E, delivery is not success; the user must be naturally guided into the action that the bot can route.

**Fix applied:** `bridge.mjs` now sends ASK_OWNER Telegram messages with normal owner copy, hides the debug POST fallback unless `CLAWROOM_DEBUG_OWNER_REPLY=true`, and uses Telegram `ForceReply` so the client prompts the owner to reply to the exact message that has a binding.

**Second fix applied:** `waiting_owner` now has an expiry path. If the owner reply is not observed before `expires_at`, the bridge closes without approving the exception and records `stop_reason: owner_reply_timeout` instead of hanging forever.

**Result:** Follow-up room `t_2fbfc1f7-f66` passed strict T3 v1 average-user owner reply: Telegram Desktop displayed the ForceReply affordance, Codex replied through the real Telegram UI, OpenClaw inbound routed the reply to relay with `source: telegram_inbound`, and both bridges reached mutual close. Redacted artifact: [`docs/progress/v3_1_t_2fbfc1f7-f66.redacted.json`](progress/v3_1_t_2fbfc1f7-f66.redacted.json). Cropped Telegram evidence: [`docs/progress/screenshots/t_2fbfc1f7-f66-owner-reply.png`](progress/screenshots/t_2fbfc1f7-f66-owner-reply.png).

**Lesson:** ASK_OWNER is not just a protocol event. It is a human interruption. The passing criterion must include a real Telegram reply from the owner account with `source: telegram_inbound`, and the fallback behavior must be explicit when the owner does nothing.

### AM. Version Strings Are Not Enough for Runtime Assets

**What:** The average-user product-path E2E uncovered a split-brain bridge install. Local clawd launched `/private/tmp/clawroom-v3/bridge.mjs`, which still reported `v3.1.0` but did not contain the Telegram ASK_OWNER binding writer. Railway Link had a newer path, so Tom-side `owner_reply` was `source: telegram_inbound`, while George-side reply fell back through the main agent and was POSTed without `source`.

**Symptom:** Room `t_93dc5ede-d2d` reached both owner loops and closed, but the host-side `owner_reply` text was rewritten in English and lacked `source: telegram_inbound`. The bridge log also lacked `ASK_OWNER Telegram binding written`.

**Fix:** The launcher now refuses a bridge missing required feature markers, now starting with the portable `owner-reply-url` capability, and reports `bridge_sha256` plus `required_features` in launch JSON. `bridge.mjs` writes `bridge_features` into runtime-state. `SKILL.md` and the Telegram E2E bootstrap prompt now pass `--require-features owner-reply-url`.

**Operational fix:** Updated the downloadable gist bundle used by `/tmp/clawroom-v3` installs so new self-launched runtimes fetch the feature-gated launcher and bridge.

**Lesson:** A runtime asset can be stale while its semantic version still looks current. Product E2E must verify capabilities, not just `bridge_version`.

### AN. Relay Network Calls Need Retry Because Idempotency Already Exists

**What:** Room `t_cf09a77b-543` proved host-side Telegram inbound owner reply, then the host bridge crashed on a transient TLS `ECONNRESET` while posting the next relay event.

**Why it mattered:** The relay POST paths already use idempotency keys. Without network retry, the system paid the complexity cost of idempotency but still died on a one-off socket reset.

**Fix:** `bridge.mjs` now retries relay fetches, including mutating calls, with the same idempotency key. The recovered room resumed from cursor after restart, posted no duplicate messages, and reached mutual close.

**Clean result:** Follow-up room `t_867a3a94-479` passed the full average-user product path without operator restart: Telegram create-room request, Telegram invite handoff to Railway Link, above-ceiling ASK_OWNER, ForceReply owner approval with `source: telegram_inbound`, mutual close, and both runtimes stopped. Redacted artifact: [`docs/progress/v3_1_t_867a3a94-479.strict-t3-clean.redacted.json`](progress/v3_1_t_867a3a94-479.strict-t3-clean.redacted.json).

**Validator fix:** The validator now treats Chinese `接受` as approval, in addition to `同意` / `批准` / `授权` / `可以` / `允许` / `通过`.

**Lesson:** Idempotency without retry is a half-built safety net. Once writes are idempotent, retry transient relay/network failures by default and make the validator bilingual for owner approvals.

### AO. Product Launch Output Needs a Code Boundary, Not a Prompt Boundary

**What:** The v3.1 average-user tests showed that launch commands naturally produce raw machine data: bearer-token invite URLs, launcher JSON, PIDs, runtime-state paths, log paths, and bridge hashes. Telling the LLM "do not show this" helps, but it is still a prompt-dependent seam.

**Fix:** Added `clawroomctl.mjs` as the product-facing wrapper. It creates or joins rooms, starts `launcher.mjs`, writes full machine details to local state, and prints only safe owner-facing JSON by default. `--debug` is opt-in. The relay now emits a public guest invite shape `/i/:thread_id/:code`, so owners forward a tokenless public URL instead of `/join?token=...`.

**Skill change:** `SKILL.md` now instructs OpenClaw to use `node clawroomctl.mjs create` and `node clawroomctl.mjs join` first. Raw `web_fetch` and direct `launcher.mjs` usage are compatibility/debug paths, not the product path.

**Lesson:** If a runtime command can print secrets or implementation details, do not rely on the agent to summarize it safely. Put a small code boundary in front of it and make the default stdout owner-safe.

### AP. Free-Tier Relay Quota Is Now a Launch Blocker, Not a Research Detail

**What:** The 2026-04-17 stability matrix passed three real cross-machine rooms, then a wrapper smoke hit Cloudflare's Durable Objects free-tier error: `Exceeded allowed volume of requests in Durable Objects free tier.`

**Why it matters:** The architecture is not fundamentally wrong. The same relay handled three consecutive cross-machine runs: average calendar scheduling, product launch communication, and a term-sheet negotiation with real Telegram `owner_reply` on the Railway Link side. But a user-facing beta cannot depend on an exhausted free-tier relay. Cloudflare's current docs state that Workers Free Durable Objects include 100,000 requests/day and further operations fail after a free-tier limit is exceeded; daily free limits reset at 00:00 UTC.

**Follow-up finding:** The three normal E2E rooms should not be anywhere near 100,000 Durable Object requests. The quota hit was followed by a local process sweep that found stale `bridge.mjs` processes from old/manual tests, some with fake or expired room ids/tokens. Those processes exposed Lesson AQ.

**Fixes applied:** The E2E harness and validator now retry transient relay fetches, so one network blip does not create a false failed artifact. `bridge.mjs` now treats stale auth/room errors as terminal and backs off on relay quota/server errors. This lowers accidental burn, but does not remove the product requirement for Workers Paid or a paid/staging relay before outside users.

**Next decision:** Before inviting outside users, move the relay to Workers Paid or deploy a paid/staging relay for E2E. Then re-run wrapper smoke and at least one product-path average-user flow on the non-exhausted relay.

**Lesson:** Passing E2E on free infrastructure is not the same as production readiness. Once the system uses long-polling Durable Objects, quota/billing is part of the product surface.

### AQ. Stale Bridges Must Not Convert Relay Errors Into Empty Polls

**What:** After the 2026-04-17 quota error, the local machine had leftover `bridge.mjs` processes from earlier experiments, including fake/stale thread ids and tokens. Some had been alive for roughly 15 hours.

**Root cause:** `bridge.mjs` previously accepted non-2xx relay responses as JSON. Then `getMessages()` coerced non-array responses into `[]`. For a stale room/token, `/messages` returned immediately with an error shape, the bridge interpreted that as "no messages", and the main loop continued instead of waiting for a real long-poll. Multiple stale bridges can therefore burn Durable Object requests much faster than a real room.

**Fix:** `relayFetch()` now throws on non-2xx responses unless that status is explicitly allowed by the caller. `401`, `403`, and `404` are fatal because they mean the bridge is unauthorized or the room is gone. `429` and Cloudflare quota errors back off for 60 seconds; 5xx errors back off before retrying the loop. State fetch failures no longer fall through to message polling.

**Verification:** A local fake relay returning `404` makes the guest bridge exit with runtime state `failed` instead of polling forever. `node --check bridge.mjs` passes.

**Lesson:** Long-poll clients must distinguish "empty poll" from "relay error". Empty is a normal state; unauthorized/not-found is a shutdown condition; quota/server errors need explicit backoff. Otherwise a background daemon turns a harmless stale test into a quota burner.

### AR. Hosted Relay Needs Admission Control; Public Install Should Prefer BYO

**What:** Once ClawRoom can be installed by outside agents, the relay billing owner becomes part of the product boundary. If the public skill defaults to George's hosted relay, any install can create rooms and consume George's Cloudflare Worker/Durable Object quota.

**Risk:** The per-room tokens protect room contents, but they do not protect `/threads/new` itself. A random external user, stale agent, or script can create many rooms or keep bridge loops alive. On Free this exhausts quota; on Paid it becomes a billable abuse path.

**Fix applied:** The relay now supports private-beta create admission: `CLAWROOM_CREATE_KEYS`, `CLAWROOM_REQUIRE_CREATE_KEY`, `CLAWROOM_CREATE_DISABLED`, and optional `CREATE_RATE_LIMITER`. BYO relays remain agent-friendly because a fresh deployment with no create keys configured still works by default. `clawroomctl.mjs` and the E2E harness now create rooms via `POST /threads` and send the create key in `X-Clawroom-Create-Key` instead of putting it in the URL.

**Hard caps added:** Each room has configurable TTL, message count, text length, and heartbeat minimum interval. Defaults are 2 hours, 120 messages, 8,000 text chars, and 10 seconds between heartbeats per role.

**BYO path:** Added `skills/deploy-clawroom-relay/SKILL.md` so an owner can hand the repo to an agent and have that agent deploy a user-owned Cloudflare Worker + Durable Object relay with a create key, smoke test, and owner-safe handoff.

**Lesson:** Public runtime distribution and hosted infrastructure are separate products. The runtime can be viral; the hosted relay must be gated. For this stage, hosted relay means private beta, while public install should make BYO relay easy.

### AS. BYO Relay Needs an Agent-Friendly Deploy Path and a Tunnel E2E Escape Hatch

**What:** After the hosted relay hit the Durable Objects free-tier volume limit, room `t_1f97d969-595` ran against a local `wrangler dev` relay exposed through an ngrok HTTPS tunnel. The guest was still Railway Link, so this was a real cross-machine Telegram self-launch test without consuming the hosted DO.

**Result:** Both OpenClaw runtimes launched bridges from Telegram, both wrote heartbeats, the relay transcript reached host -> guest -> host close -> guest close, and both runtime heartbeats ended with `status: stopped`. Redacted artifact: [`docs/progress/v3_1_t_1f97d969-595.byo-local-tunnel.redacted.json`](progress/v3_1_t_1f97d969-595.byo-local-tunnel.redacted.json).

**Operational note:** Cloudflare Quick Tunnel failed on this machine with repeated edge TLS EOFs, likely from the local network/VPN path to 198.18.*. ngrok worked and produced clean JSON API responses, though it had reconnect/heartbeat noise and the host bridge recovered from early relay `503` / fetch failures via retry. Tunnel E2E is useful as a quota-saving validation path, but it should not be sold as the normal install path.

**Coverage limit:** This was a 2-message smoke run. It proves BYO relay transport and cross-machine self-launch, not T3 ASK_OWNER or product-safe average-user wrapper copy.

**Lesson:** A public-ready BYO story needs two layers: an agent-friendly deployment skill for durable user-owned Cloudflare Workers, plus a temporary tunnel path for cheap E2E during development. Treat tunnels as test infrastructure, not production infrastructure.

### AT. Hosted Gating Passed, But Launch-Time Agent Readiness Needs Its Own Gate

**What:** On 2026-04-18, after the daily quota window recovered, the production hosted relay accepted create-key authenticated room creation and rejected unauthenticated creation. That proved the hosted admission-control path was deployed and usable. Two Telegram self-launch smoke attempts then reached both bridge heartbeats but failed before negotiation: the host bridge timed out while asking local OpenClaw for the opening message.

**Evidence:** Failed redacted artifacts are committed at [`docs/progress/v3_1_t_5d82f11e-e4d.hosted-gated-smoke.failed.redacted.json`](progress/v3_1_t_5d82f11e-e4d.hosted-gated-smoke.failed.redacted.json) and [`docs/progress/v3_1_t_f4454ea3-924.hosted-gated-smoke-rerun.failed.redacted.json`](progress/v3_1_t_f4454ea3-924.hosted-gated-smoke-rerun.failed.redacted.json). Both show `closed: true` after manual cleanup, guest stopped cleanly, host remained at `status: starting`, and `message_count: 0`. That means relay creation, invite delivery, and bridge launch worked; the negotiation never began.

**Root cause narrowed:** The local OpenClaw gateway service was initially split-brain: `openclaw --version` was `2026.4.15-beta.1`, but the LaunchAgent still pointed to an older npm install whose config schema rejected the current `openclaw.json`. Reinstalling the LaunchAgent fixed the stale binary problem, but the full Telegram launch still timed out once. Direct preflight then proved the current gateway and `clawroom-relay` agent were healthy: `openclaw agent --agent clawroom-relay ...` returned `REPLY: PONG`, and a raw bridge-shaped `method:"agent"` WebSocket call returned successfully.

**Fix applied:** `bridge.mjs` is now `v3.1.1`. It no longer relies only on the final WebSocket `res` after `status:"accepted"`; it also accepts OpenClaw `agent` assistant/lifecycle events and `chat` final events for the same `runId`. The OpenClaw timeout is configurable through `CLAWROOM_AGENT_TIMEOUT_MS` and defaults to 240 seconds instead of 90 seconds. Fatal top-level bridge errors now write a `failed` relay heartbeat before exit, so future artifacts show the actual failed state instead of leaving the host stuck at `starting`.

**Verification:** `node --check bridge.mjs` passes. A local bridge smoke against the hosted gated relay created room `t_1f9480ad-721`, started `bridge.mjs v3.1.1`, received an OpenClaw accepted run, posted the opening message `"Relay agent online, ready for coordination."`, observed cleanup close, and exited with code 0. The self-download gist bundle was refreshed; raw `bridge.mjs` SHA-256 is `4c257bf8257bdd8282645cd5f1db119a145fc02a942cc461b09466c3f22d938e`. A follow-up real Telegram cross-machine smoke, room `t_efa33869-432`, passed with host and guest both on `bridge_version: v3.1.1`, 2 negotiation messages, mutual close, and both runtime heartbeats stopped. Redacted artifact: [`docs/progress/v3_1_t_efa33869-432.hosted-gated-v311-smoke.redacted.json`](progress/v3_1_t_efa33869-432.hosted-gated-v311-smoke.redacted.json).

**Lesson:** Gateway health, Telegram provider health, and relay create success are not enough before a real E2E. The preflight gate must include one actual `clawroom-relay` agent turn using the same WebSocket method/session shape as the bridge. Self-launched bridges also need enough startup tolerance for the launching OpenClaw turn to finish and for async gateway events to arrive.

### AU. ASK_OWNER Recovery Must Not Fall Through to the Main Agent

**What:** Strict H1 room `t_1651a049-2f9` reached a real host-side `ASK_OWNER` on the hosted gated relay. Telegram displayed the owner decision message, and the bridge wrote the `(chat_id, message_id) -> owner_reply_token` binding. The owner replied through Telegram, but the first owner-reply relay call hit a transient `fetch failed`, so the bot told the owner to "reply again in a moment."

**Average-user failure:** The second owner message was a normal Telegram message, not a reply to the original `ASK_OWNER` message. The old inbound handler treated non-replies as normal chat, so the main OpenClaw agent consumed the authorization text and the host bridge posted it as a regular room message. The room then closed at `JPY 75,000`, above the host `JPY 65,000` mandate, with no `owner_reply` event and no `source: telegram_inbound`.

**Evidence:** Failed redacted artifact: [`docs/progress/v3_1_t_1651a049-2f9.H1-failed-telegram-owner-reply-fallthrough.redacted.json`](progress/v3_1_t_1651a049-2f9.H1-failed-telegram-owner-reply-fallthrough.redacted.json). Screenshots show the technical launcher JSON still visible above the clean owner prompt, the first ForceReply attempt failing with "Could not reach ClawRoom", and the second non-reply message falling through to the main agent: [`docs/progress/screenshots/t_1651a049-2f9-owner-ask-visible-json-above.png`](progress/screenshots/t_1651a049-2f9-owner-ask-visible-json-above.png), [`docs/progress/screenshots/t_1651a049-2f9-owner-approval-failed-reach.png`](progress/screenshots/t_1651a049-2f9-owner-approval-failed-reach.png), [`docs/progress/screenshots/t_1651a049-2f9-owner-retry-normal-message-no-quote.png`](progress/screenshots/t_1651a049-2f9-owner-retry-normal-message-no-quote.png), [`docs/progress/screenshots/t_1651a049-2f9-owner-fallthrough-main-agent.png`](progress/screenshots/t_1651a049-2f9-owner-fallthrough-main-agent.png).

**Fix direction at the time:** a deployment-specific OpenClaw Telegram inbound adapter can retry transient owner-reply relay failures, treat `409 owner_reply_already_consumed` as already recorded, and add a fallback route for average-user recovery.

**Correction:** This cannot be the portable ClawRoom product path. Other OpenClaw installs will not inherit local source/fork changes. The portable path must be ClawRoom-owned: send an owner decision URL that posts directly to the relay and records `owner_reply.source: owner_url`.

**Validator fix:** `bridge.mjs` and `scripts/validate_e2e_artifact.mjs` now parse currency-prefix amounts such as `JPY 75,000`, so mandate checks catch above-ceiling closes in English/Japanese money notation.

**Lesson:** ForceReply is a helpful affordance, not a guarantee. When recovery copy says "reply again", a normal owner will often send a plain message. For arbitrary OpenClaw hosts, do not rely on Telegram inbound interception; use a ClawRoom-owned owner decision URL.

### AV. Direct Telegram Harness Output Is Not the Product Path

**What:** Follow-up 2026-04-19 screenshot checks proved two different UX facts that must stay separate. First, strict H1 room `t_aa6c678f-12f` passed the real `ASK_OWNER` Telegram inbound path with `owner_reply.source: telegram_inbound`, mutual close, and both runtimes stopped. Second, the direct Telegram E2E harness still displayed a technical command block as the user input because it asks the agent to download and run `launcher.mjs` directly.

**Fix applied:** `launcher.mjs` now supports `--owner-facing`. In that mode, stdout is a single owner-safe sentence, not launcher JSON. It hides PIDs, `bridge_sha256`, runtime paths, log paths, state dirs, tokens, and logs even if the agent pastes command output back into Telegram. The Telegram E2E harness now refreshes the exact bundle every run and uses `--owner-facing`.

**Verification:** Average calendar room `t_d3367a68-dd6` passed with 2 negotiation messages and mutual close; term-sheet room `t_08592cec-253` passed with 8 negotiation messages, all required term-sheet fields present, mutual close, and both runtimes stopped. Screenshots show the bot reply/final report no longer includes raw launcher JSON. The screenshots also show the direct harness command block itself, so these are bridge-regression UX checks, not full average-user product-path screenshots. Redacted artifacts: [`docs/progress/v3_1_t_d3367a68-dd6.A1-average-calendar-safe-bootstrap.redacted.json`](progress/v3_1_t_d3367a68-dd6.A1-average-calendar-safe-bootstrap.redacted.json), [`docs/progress/v3_1_t_08592cec-253.H4-term-sheet-8-turns.redacted.json`](progress/v3_1_t_08592cec-253.H4-term-sheet-8-turns.redacted.json), and [`docs/progress/v3_1_t_aa6c678f-12f.H1-passed-telegram-owner-reply.redacted.json`](progress/v3_1_t_aa6c678f-12f.H1-passed-telegram-owner-reply.redacted.json).

**Remaining product UX risk:** The Railway Link bot emitted unrelated memory/persona chatter before one launch confirmation. It did not affect the relay room, but it is not acceptable for polished public use. The next product-path gate must use the actual `clawroomctl`/skill/public-invite flow, not a direct command-block harness.

**Lesson:** Code can make command stdout safe, but it cannot make a command-block prompt feel like an average-user flow. Count direct harness screenshots as regression evidence only. Count average-user readiness only when the Telegram-visible path is natural language plus a public invite, with internal commands hidden by the skill/runtime.

### AW. Public Invites Must Dispatch to Verified Bridge Join, Not Main-Agent Chat

**What:** The first natural-language product-path public invite run, room `t_423bc8e2-d37`, proved the host side of the wrapper path but failed on the guest side. Local clawd received an ordinary Telegram request, invoked the installed `clawroom-v3` skill, used `clawroomctl create`, launched bridge `v3.1.1`, and returned a public invite URL without exposing fresh JSON, PID, state paths, log paths, hashes, or raw bearer tokens.

**Guest failure:** Railway Link received the public `/i/:thread/:code` invite in normal Telegram language and posted guest text into the room, but it did not launch a v3 verified bridge. The relay snapshot had only a host runtime heartbeat. The transcript reached host message -> guest message -> host close -> guest message, then needed manual host/guest cleanup close events. That is not an autonomous mutual close.

**Evidence:** Failed redacted artifact: [`docs/progress/v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json`](progress/v3_1_t_423bc8e2-d37.product-path-guest-no-verified-bridge.failed.redacted.json). Screenshots: [`docs/progress/screenshots/t_423bc8e2-d37-product-path-host-public-invite.png`](progress/screenshots/t_423bc8e2-d37-product-path-host-public-invite.png), [`docs/progress/screenshots/t_423bc8e2-d37-product-path-guest-public-invite-with-chatter.png`](progress/screenshots/t_423bc8e2-d37-product-path-guest-public-invite-with-chatter.png).

**Root cause follow-up:** Railway logs around the guest invite show the Link agent tried a normal tool path, `canvas navigate`, against the public invite URL. Remote inspection then showed `openclaw skills list` did not contain `clawroom` or `clawroom-v3`. The only ClawRoom skill files on the Railway workspace were legacy v2.2.0 copies under other agent directories such as `/data/workspace/.codebuddy/skills/clawroom` and `/data/workspace/.continue/skills/clawroom`; OpenClaw's visible workspace root `/data/workspace/skills` had no ClawRoom skill. That legacy skill also triggered on `api.clawroom.cc/join/`, not the v3 `/i/:thread/:code` invite shape.

**UX finding:** The new product-path bot replies did not paste launcher JSON, PIDs, paths, hashes, or raw room tokens. But Telegram rendered the public invite URL as a `CR-...json` download card because `/i/:thread/:code` returned `application/json`. That still counts as product-facing technical leakage. Both bots also produced conversational noise, especially Railway Link's unrelated memory/persona chatter.

**Fix applied:** The relay public invite route now returns a human-safe HTML preview by default. It returns machine JSON only when the caller sends `Accept: application/json` or `?format=json`. `clawroomctl join` now sends `Accept: application/json`. Hosted relay version `7e09fc20-806d-42fa-b867-12d8ce300d2a` is deployed and curl-verified for default HEAD/GET HTML plus JSON HEAD/GET.

**Fix direction still open:** First, make the v3 skill visible to OpenClaw in the active workspace install path (`/data/workspace/skills/<skill>/SKILL.md` on Railway, or native `openclaw skills install` / ClawHub flow for real users). Then add public invite URL handling as a code-level routing hook in OpenClaw/Link: detect `clawroom-v3` public invite URLs, dispatch to `clawroomctl join`, verify the guest bridge heartbeat, and suppress normal main-agent chatter while the skill is launching. The main agent may explain the result after the skill returns, but it must not free-form negotiate or post room messages as a substitute for the verified bridge.

**Lesson:** A public invite is a bootstrap protocol message, not a chat topic. If it falls through to the main agent, the room can look alive while missing the runtime properties that make ClawRoom reliable: isolated session key, heartbeat, cursor, close handling, retry, owner-reply bindings, and clean shutdown.

### AX. OpenClaw-Visible Skill Install Is Part of the Runtime Contract

**What:** After the `t_423bc8e2-d37` product-path failure, the current v3 skill bundle was installed into Railway Link's OpenClaw-visible workspace at `/data/workspace/skills/clawroom-v3`. The same bundle was synced into local clawd's visible skill path under `~/clawd/skills/clawroom-v3`.

**Verification:** Remote `OPENCLAW_STATE_DIR=/data/.openclaw openclaw skills info clawroom-v3` returned `Ready`, remote `node --check` passed for `clawroomctl.mjs`, `launcher.mjs`, and `bridge.mjs`, and local/remote SHA-256 matched for `SKILL.md`, `clawroomctl.mjs`, `launcher.mjs`, and `bridge.mjs`.

**Result:** Room `t_71abe35b-cd9` passed the real natural-language product path. Local clawd created the room and public invite from Telegram; Railway Link received the public invite in Telegram and launched the guest bridge inside the Railway container. Relay transcript reached host message -> guest message -> host close -> guest close. Host runtime ended `status: stopped`, `stop_reason: own_close`; guest runtime ended `status: stopped`, `stop_reason: peer_close`. Redacted artifact: [`docs/progress/v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json`](progress/v3_1_t_71abe35b-cd9.product-path-visible-skill-passed.redacted.json). Screenshot evidence: [`docs/progress/screenshots/t_71abe35b-cd9-telegram-after-close.png`](progress/screenshots/t_71abe35b-cd9-telegram-after-close.png).

**UX result:** The fresh public invite rendered as a human `ClawRoom Invite` preview, not a `CR-...json` download card. The new Railway Link owner summary did not expose launcher JSON, PID, paths, hashes, raw bearer tokens, or logs. The screenshot still contains older historical technical messages above the new run, so future polished release gates should capture clean cropped host and guest chat evidence.

**Operational note:** A post-run local process sweep found no live bridge. Railway `ps` found no live `bridge.mjs --thread ...` command, but did show historical `[node] <defunct>` zombie rows including the exited guest PID. That is a process-reaping hygiene issue, not proof of an active relay poller.

**Runbook:** Future agents should start from [`docs/runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`](runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md). It separates local clawd from Railway Link, includes the stale-process preflight, explains where to find runtime files and logs, and documents the artifact/screenshot standard.

**Lesson:** Skill installation is not just "files exist somewhere." The skill must be visible to the exact OpenClaw runtime that receives the Telegram update. For Railway Link, that means `/data/workspace/skills/clawroom-v3` plus `OPENCLAW_STATE_DIR=/data/.openclaw` verification. Legacy skill copies under other agent directories do not count.

### AY. Owner Constraints Are Bidirectional Mandates

**What:** Product-path strict T3 room `t_fbc2bcd0-57e` proved that a transport-correct ClawRoom can still be semantically wrong. The room reached mutual close with both bridges stopped, but the guest accepted `JPY 64,000` even though Tom's owner context said the bottom price was `JPY 75,000`.

**Root cause:** The bridge already enforced host-side `budget_ceiling_jpy`, but it did not treat guest-side minimum/floor language as an owner mandate. The guest owner context was also easier for the skill to drop because public-invite joins inherited the room goal but did not reliably preserve the guest's local negotiation constraints.

**Fix applied:** `bridge.mjs` now parses `price_floor_jpy` plus natural floor language such as floor, bottom, lowest, minimum, `底价`, `最低`, `不低于`, and `至少`. `SKILL.md` now tells the host and guest paths to build `OWNER_CONTEXT` from the actual owner message, not just from the room goal, and to include machine-readable mandate lines when natural constraints are present. The validator now reports `guest_floor_compliance` and fails closes below the guest floor unless an owner approval exists.

**Verification:** Product-path strict T3 room `t_ebfeb7da-0b6` passed with ordinary Telegram language, not a direct command harness. Guest Link asked Tom before accepting below `JPY 75,000`; the owner rejected through Telegram and the event landed as `owner_reply.source: telegram_inbound`. Host clawd then asked George before accepting above the `JPY 65,000` ceiling; that owner approval also landed as `telegram_inbound`. The room closed at `JPY 75,000`, both sides stopped, and the self-validating redacted artifact is [`docs/progress/v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json`](progress/v3_1_t_ebfeb7da-0b6.product-path-strict-t3-bidirectional-owner-reply.redacted.json).

**UX follow-up:** The passing screenshots no longer showed launcher JSON, PIDs, hashes, raw tokens, paths, or logs. However, the ASK_OWNER notification still exposed internal `Room` and `Role` labels. The bridge now hides those by default and shows them only when `CLAWROOM_DEBUG_OWNER_REPLY=true`; Lesson AZ records the follow-up screenshot gate.

**Lesson:** Product pass requires business-rule compliance, not just mutual close. Mandates are owned by both sides: host ceilings, guest floors, deadlines, approval requirements, and other owner constraints must all be enforced before an agent can close.

### AZ. Owner-Facing Authorization Copy Must Hide Runtime Labels By Default

**What:** The first passing bidirectional owner-reply screenshots for `t_ebfeb7da-0b6` proved protocol correctness, but the ASK_OWNER notification still exposed internal `Room` and `Role` labels. Those labels are not secrets, but they make a normal Telegram decision prompt feel like an operator console.

**Fix applied:** `bridge.mjs` now hides `Room`, `Role`, and owner-reply endpoint details by default. They only appear when `CLAWROOM_DEBUG_OWNER_REPLY=true`. The updated bridge bundle was synced to both local clawd and Railway Link's OpenClaw-visible skill path.

**Verification:** Product-path copy gate room `t_f6997679-d1b` passed with a natural-language host create, public invite, Railway Link guest join, real Telegram owner approval, mutual close, and both runtimes stopped. The screenshot-backed ASK_OWNER prompt no longer showed `Room`, `Role`, launcher JSON, PID, paths, hashes, raw bearer tokens, or logs. Redacted artifact: [`docs/progress/v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json`](progress/v3_1_t_f6997679-d1b.product-path-ask-owner-copy-clean.redacted.json).

**Remaining UX note:** The copy is still partly English and Railway Link still emitted a small unrelated persona greeting before launch. These are OpenClaw-owned behavior, not ClawRoom blockers, as long as ClawRoom does not leak runtime details and the skill still launches.

**Lesson:** Debug context belongs behind an explicit debug flag. Owner-facing authorization should read like a decision request, not like runtime telemetry.

### BA. Close Summaries Need an Explicit Product Oracle

**What:** Product-path H4 room `t_5edced11-e61` transported correctly but failed the term-sheet oracle. Both bridges launched, negotiated, closed, and stopped, yet the final close summary omitted the required next step.

**Root cause:** The bridge prompt treated close as "finish the room" rather than "produce an owner-ready result." The original goal and close-summary requirements were not strong enough on the final turn, so the model could produce a plausible but incomplete close.

**Fix:** `bridge.mjs` now includes `Goal:` in every turn prompt and requires the close summary to include key fields plus an explicit next step before emitting `CLAWROOM_CLOSE:`.

**Verification:** Product-path rerun `t_c3baf829-11c` passed with 8 negotiation messages, 3 real Telegram inbound owner replies, all required H4 fields, mutual close, and both runtimes stopped. Redacted artifact: [`docs/progress/v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json`](progress/v3_1_t_c3baf829-11c.H4-product-path-term-sheet-8-turns.redacted.json).

**Lesson:** Mutual close is a transport condition, not a product result. Hard scenarios need explicit oracle fields such as price, deliverables, usage, payment, approvals, cancellation, confidentiality, and next step.

### BB. Source Tests Do Not Prove the Running OpenClaw Package

**What:** Non-reply recovery failed even though the OpenClaw source tree already contained fallback logic. The hosted Railway Link process was serving a packaged Telegram extension under the installed OpenClaw `dist`, and that bundle did not include the source patch.

**Symptom:** Room `t_11cd6ca3-5e7` reached a real guest-side `ASK_OWNER`; after Command+Down cancelled Telegram ForceReply, the owner sent a plain approval, but no `owner_reply` event appeared.

**Root cause:** We verified the source tree and local package, but not the exact packaged JavaScript bundle loaded by the Railway container's running OpenClaw entrypoint.

**Fix:** The Railway package bundle was hotpatched and the OpenClaw gateway process restarted; source tests now cover the non-reply path. The clean run `t_73240be6-5b6` then passed with `owner_reply.source: telegram_inbound`, mutual close, and cropped Telegram evidence.

**Correction:** This is adapter evidence, not ClawRoom product readiness. A hotpatch can prove what happened in one deployment, but ClawRoom must not require OpenClaw or Clawdbot source changes for public use.

**Lesson:** For optional Telegram/OpenClaw adapter behavior, check the running artifact, not just the repository. For ClawRoom core, avoid this dependency entirely by keeping owner authorization inside ClawRoom-owned URLs and relay POSTs.

### BC. Non-Reply Owner Fallback Must Exclude Launch And Invite Text

**What:** The first broad non-reply fallback created a new failure class. With a stale pending ASK_OWNER binding, a ClawRoom launch or invite prompt could be interpreted as an owner decision and posted as `owner_reply`.

**Why it matters:** ASK_OWNER fallback is intentionally a code-level recovery path for average users, but launch/invite prompts can contain room ids, public URLs, role hints, and command-like text. Treating those as owner decisions corrupts the room and can also consume a valid binding.

**Fix for the optional adapter:** Telegram inbound should reject likely ClawRoom launch/invite/token-bearing texts before single-binding fallback routing. Tests should cover prompts containing ClawRoom launch requests, `node launcher.mjs`, `--thread`, `--token`, `/join?token=`, and role hints.

**Operational fix:** Before rerunning the clean gate, stale ASK_OWNER binding JSON files were archived from both local and Railway state dirs.

**Lesson:** Recovery routing must be narrower than normal chat routing. Better: do not make this ClawRoom's portable path. A relay-owned decision URL avoids sharing Telegram inbound with the host runtime.

### BD. Real Product E2E Uses Public Invite Language, Not Technical Harness Prompts

**What:** After adding the launch/invite guard, a direct technical guest prompt no longer reliably triggered the skill in the same way a normal product invite did. The clean non-reply recovery pass used a public invite URL plus ordinary Telegram language to Railway Link.

**Why it matters:** The direct harness is still useful for protocol regression, but it is not an average-user UX proof. The product gate must test how a normal owner actually behaves: forward the public invite, type constraints naturally, approve/reject in Telegram, and inspect screenshots.

**Verification:** Room `t_73240be6-5b6` passed the non-reply recovery gate for the tested deployment-specific adapter. Screenshot evidence shows the owner sent a plain non-reply approval after Command+Down and the bot confirmed `Authorization recorded.` Redacted artifact: [`docs/progress/v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json`](progress/v3_1_t_73240be6-5b6.nonreply-owner-recovery-clean.redacted.json).

**Lesson:** Count direct harnesses as transport/protocol tests. Count public readiness only from natural-language Telegram product paths with screenshots and self-validating redacted artifacts. Owner authorization should use `owner_url` unless the test is explicitly scoped to an optional host-runtime adapter.

### BE. ClawRoom Must Not Require OpenClaw Source Patches

**What:** During the non-reply Telegram investigation, local source checkouts `project/openclaw` and `project/clawdbot` were treated as if they were part of the ClawRoom release path. That was the wrong boundary.

**Why:** The actual Telegram runtimes are installed OpenClaw packages: local clawd and Railway Link. A source checkout under `~/Desktop/project` is not automatically connected to either runtime, and even a correct local patch will not propagate to future users' OpenClaw installs.

**Correction applied:** ClawRoom core now treats Telegram inbound interception as an optional adapter only. The portable owner authorization path is a ClawRoom-owned decision URL served by the relay:

1. bridge posts `ask_owner`;
2. relay returns `owner_reply_url`;
3. bridge sends a Telegram URL button;
4. owner opens the ClawRoom page and submits a decision;
5. relay records `owner_reply.source: owner_url`;
6. bridge resumes.

**Implementation correction:** `owner-reply` GET renders a non-mutating decision page. The write remains POST-only, so link previews cannot consume the decision. The launcher feature gate now requires `owner-reply-url`, not `telegram-ask-owner-bindings`. Telegram inbound binding writes are disabled by default and require `CLAWROOM_ENABLE_TELEGRAM_INBOUND_BINDINGS=true`.

**Lesson:** ClawRoom can run inside OpenClaw, but it should not require modifying OpenClaw. Host-runtime integration is an adapter; relay/bridge/skill behavior is the product.

### BF. Owner URL Is The Portable ASK_OWNER Gate

**What:** Room `t_34182ff8-eba` passed the portable public-core ASK_OWNER path on real local clawd plus Railway Link. The host created a room from a normal Telegram prompt, Link joined from a public invite, guest rejected a below-floor `JPY 55,000` offer through the ClawRoom decision page, host approved an above-ceiling `JPY 75,000` offer through the ClawRoom decision page, and both bridges reached mutual close.

**Evidence:** Redacted artifact [`docs/progress/v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json`](progress/v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json) validates with:

```sh
node scripts/validate_e2e_artifact.mjs --artifact docs/progress/v3_1_t_34182ff8-eba.product-path-owner-url-bidirectional.redacted.json --require-ask-owner --require-owner-reply-source owner_url --min-events 8 --min-messages 2
```

**Validator result:** 8 relay events, 2 negotiation messages, 2 close events, 2 `ask_owner` rows, 2 concrete `owner_reply` rows, both `owner_reply.source` values equal `owner_url`, role/question matching passed, host and guest runtimes stopped, final close at `JPY 75,000`, host ceiling exceeded only with owner approval, and guest floor respected.

**UX evidence:** Screenshots show the public invite preview rendered as `ClawRoom Invite`, not a JSON download card; ASK_OWNER Telegram messages showed owner-readable copy plus an `Open Decision Page` button; the decision pages showed a plain textarea/form and confirmation page; fresh owner-facing ClawRoom messages did not show launcher JSON, PIDs, file paths, hashes, raw room tokens, create keys, or bridge commands.

**Caveat found:** The first Railway Link invite attempt returned `Agent couldn't generate a response` with an OpenClaw log `incomplete turn detected ... payloads=0`. A shorter ordinary retry succeeded and launched the guest bridge. This is a product resilience issue in the OpenClaw generation/skill trigger layer, not a relay/bridge correctness failure.

**Lesson:** Treat `owner_url` as the required portable ASK_OWNER proof. Treat `telegram_inbound` and non-reply recovery as optional deployment-adapter proof only. Future public-readiness E2E should require `--require-owner-reply-source owner_url` unless the test is explicitly scoped to a specific OpenClaw Telegram adapter.

### BG. Release-Candidate E2E Starts With A Clean Visible Runtime

**What:** Room `t_5b9218cb-cb8` reran the H1 owner-url product path after cleaning both actual Telegram OpenClaw runtimes. Local clawd's visible `~/clawd/skills/clawroom-v3` bundle was removed and reinstalled from the repo; Railway Link's visible `/data/workspace/skills/clawroom-v3` bundle and `/data/.openclaw/clawroom-v3` runtime state were cleaned and reinstalled. Both sides were preflighted before Telegram testing.

**Verification:** The run used normal Telegram product language, not a direct command harness. Local clawd launched the host bridge with PID `46247`; Railway Link launched the guest bridge with PID `43895`. The relay transcript reached host message, guest message, host `ask_owner`, host `owner_reply.source: owner_url`, host close, guest close. Final snapshot was `closed: true`; both runtime heartbeats ended `status: stopped`. Redacted artifact: [`docs/progress/v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json`](progress/v3_1_t_5b9218cb-cb8.H1-clean-reinstall-owner-url.redacted.json).

**UX evidence:** Fresh ClawRoom-owned Telegram output did not show launcher JSON, PID, runtime paths, hashes, bearer tokens, create keys, logs, or bridge commands. The ASK_OWNER decision page and confirmation were human-readable. Link's ordinary persona chatter appeared around the test but is OpenClaw-owned chatter, not a ClawRoom failure when the verified bridge launches and closes correctly.

**Sharp edge found:** Telegram Desktop `tg://resolve` opened a wrong or fresh bot view during the run. Selecting `Link_🦀` through visible Telegram search and verifying the chat title before sending avoided the problem.

**Lesson:** A release-candidate E2E is not just "run another room." First sweep stale bridges, clean or archive stale room state, reinstall the visible skill bundle in the actual local and Railway runtimes, verify hashes and `openclaw skills info`, then send ordinary Telegram messages and inspect screenshots.

### BH. Runtime Location Beats Transcript Beauty

**What:** Post-clean H4 attempt `t_f6d18ff9-c54` produced an apparently strong protocol transcript: 8 negotiation messages, mutual close, stopped runtimes, and a complete term-sheet summary. But the Telegram guest invite had been pasted into `clawd`, not `Link_🦀`, so local clawd launched both roles.

**Evidence:** The redacted artifact [`docs/progress/v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json`](progress/v3_1_t_f6d18ff9-c54.H4-contaminated-same-machine.failed.redacted.json) records both runtime locations as local and records that Railway had no guest runtime file. Screenshot [`docs/progress/screenshots/t_f6d18ff9-c54-h4-guest-invite-sent-clean-reinstall.png`](progress/screenshots/t_f6d18ff9-c54-h4-guest-invite-sent-clean-reinstall.png) shows the guest invite text under the `clawd` chat.

**Secondary finding:** The validator conservatively failed `ask_owner_evidence` because the transcript contained a rejected `JPY 95,000` proposal above the host's `JPY 90,000` ceiling. The final close was within the ceiling, so future validators may want to distinguish accepted over-ceiling terms from rejected proposals. For hard gates, the conservative failure is acceptable.

**Lesson:** A beautiful relay transcript is not enough. Every E2E artifact needs runtime-location proof: local host files, Railway guest files, distinct PIDs, stopped heartbeats, and screenshots with the intended Telegram chat title visible before paste/send.

### BI. Direct Runtime Gates Are Useful But Not UX Gates

**What:** After the `t_f6d18ff9-c54` Telegram UI misroute, room `t_4b919672-44d` reran H4 through direct installed runtime commands: local `clawroomctl.mjs create`, Railway `/data/workspace/skills/clawroom-v3/clawroomctl.mjs join`, real OpenClaw gateways, hosted relay, and `owner_url` approval.

**Verification:** The room passed with 12 relay events, 8 negotiation messages, 1 `ask_owner`, 1 `owner_reply.source: owner_url`, mutual close, local host PID `38348`, Railway guest PID `44616`, and both runtimes stopped. Final summary included price, deliverables, payment timing, usage rights, approval/revision timing, cancellation/reschedule, confidentiality/public announcement, no exclusivity, and next step. Redacted artifact: [`docs/progress/v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json`](progress/v3_1_t_4b919672-44d.H4-direct-runtime-cross-machine-owner-url.redacted.json).

**Boundary:** This is strong bridge/relay/runtime evidence, but it is not average-user Telegram launch evidence. The Telegram UI H4 path still needs a clean rerun where the operator or automation visibly confirms `clawd` for creation and `Link_🦀` for guest join before paste/send.

**Lesson:** When UI automation is noisy, split the evidence instead of pretending one run proves everything. Use direct runtime gates to keep protocol hardening moving; use screenshot-backed Telegram product gates to prove user experience.

### BJ. Computer-Use Harnesses Must Prove The Target Before Paste

**What:** The `t_f6d18ff9-c54` H4 attempt failed because the Telegram computer-use path sent the guest invite into `clawd` instead of `Link_🦀`. The bridge/relay protocol then produced an attractive 8-message transcript, but both host and guest bridges were local.

**Root cause:** The old harness trusted `tg://resolve`, global keyboard focus, clipboard paste, and fixed sleeps. It did not assert the active Telegram chat title before sending. AppleScript accessibility could confirm the Telegram window bounds but Telegram's custom UI did not expose enough structured chat-title text, so a pure Accessibility check was insufficient.

**Fix applied:** `scripts/telegram_e2e.mjs` now has a fail-closed target guard. Before paste/send it opens the target, captures a screenshot, crops the active chat title area from the Telegram window, OCRs that crop, and checks it against expected title alternatives such as `clawd` or `Link|Link_`. The guard writes screenshot and title-crop evidence into the harness artifact path. `--target-check-only` runs the guard without creating a room or sending a message. `--no-confirm-targets` exists only for local debugging and must not be used for release evidence.

**Verification:** The guard passed for the real local targets with title OCR samples `clawd bot` and `Link_... bot`, and a negative check with an impossible guest title failed before any message could be pasted. This turns the previous silent GUI contamination into an explicit pre-send failure.

**Lesson:** GUI automation is not evidence unless it includes visual target assertions. For Telegram E2E, relay validity and runtime heartbeats are necessary but not sufficient; the artifact must also prove the computer-use layer sent the message to the intended visible chat.

---

## Updates Log

- **2026-04-07** Initial document. Added all lessons from S1-S5, root cause experiments, messy user tests, and continuation hint iteration.
- **2026-04-08** Hardcore E2E re-run on the public `npx skills add heyzgj/clawroom` install path. Three rounds with subagent pairs as fresh OpenClaw installs. Drove the four real fixes that became skill v2.2.0 → v2.2.1 (open-immediately, status-shape example, fills-every-send, GET-only API surface). All rounds passed after fixes.
- **2026-04-13** v3 relay experiments. Added Q (skill keyword hijacking), R (cron.add vs background process), S (duplicate cron.add from confirm step), T (relay reliability baseline), U (v3 confirmation of LLM-as-executor failure). Updated pending item: cron path is NOT blocked by WARP DNS — blocked by Gateway service disabled state.
- **2026-04-13** C2 results. Added V (cron timing non-deterministic beyond early fires — 16min delay observed on fire 3 despite 60s nominal interval). B experiment PASSED 3/3 (main session web_fetch reliable). C2 PARTIAL PASS (all 3 pings arrived but fire 3 delayed ~16min).
- **2026-04-14** v3.1 hardening and real Telegram E2E. Added Part 7 (DO relay + verified bridge + Telegram self-launch path), lessons Z-AH, and the first passing local clawd plus Railway Link run (`t_92615621-4a8`, mutual close, 4 relay events, both owner notifications delivered). Redacted artifact co-located at [`docs/progress/v3_1_t_92615621-4a8.redacted.json`](progress/v3_1_t_92615621-4a8.redacted.json). Added Lesson AI for REPLY:/CLAWROOM_CLOSE: marker-scan robustness before the next multi-turn / ASK_OWNER E2E.
- **2026-04-15** Review fixes plus T2-full E2E. Fixed README commands, made redacted artifacts self-validating with embedded transcripts, changed "signed invite URL" wording to tokenized invite URL, hardened marker parsing, and added `--min-messages`. Room `t_f8d18771-716` is committed as a failed artifact (closed after 4 messages); room `t_0b3602a9-e3b` passed T2-full transport/runtime gates with 8 negotiation messages. Added Lesson AJ.
- **2026-04-15** T3 v0 mandate guard E2E. Added owner-reply protocol, relay control events, ASK_OWNER/mandate intercept in bridge, and auto owner-reply E2E harness support. Room `t_1f72571a-3f4` failed because a mutating GET owner-reply URL was consumed with placeholder text; fix made owner replies POST-only. Room `t_fb3fda2d-563` then passed T3 v0 with ASK_OWNER, concrete owner_reply, close at `¥65,000`, and both runtimes stopped. Added Lesson AK.
- **2026-04-15** Strict T3 v1 average-user E2E attempt. Room `t_e5f0c995-23e` reached ASK_OWNER on real local + Railway bridges and wrote the Telegram binding, but no human Telegram reply entered OpenClaw inbound before timeout. Committed a failed redacted artifact and hardened bridge owner UX with ForceReply plus `owner_reply_timeout`. Added Lesson AL.
- **2026-04-16** Strict T3 v1 average-user E2E passed. Room `t_2fbfc1f7-f66` used real Telegram Desktop ForceReply input; owner reply landed as `source: telegram_inbound`; room closed with 8 events, 4 negotiation messages, 2 close events, and both runtimes stopped. Added redacted artifact and cropped Telegram screenshot evidence.
- **2026-04-16** Average-user product-path E2E hardening. Room `t_fc9adb58-da7` passed Telegram bootstrap smoke but did not trigger ASK_OWNER. Room `t_93dc5ede-d2d` exposed stale local `/tmp` bridge assets. Added launcher feature gates, bridge feature telemetry, relay fetch retries, updated the downloadable gist bundle, and fixed validator Chinese approval detection. Room `t_cf09a77b-543` is a recovered pass after retry patch/restart. Room `t_867a3a94-479` is the clean product-path T3 pass with `source: telegram_inbound`, above-ceiling approval, mutual close, and both runtimes stopped. Added Lessons AM-AN.
- **2026-04-17** Final stability matrix and launch-boundary hardening. Added `clawroomctl.mjs`, public `/i/:thread/:code` guest invites, owner-safe skill instructions, E2E/validator fetch retries, and refreshed the self-download gist bundle. Cross-machine stability passed for `t_dba18332-f9f` (average calendar, 2 messages), `t_0babf6d2-297` (product launch comms, 4 messages), and `t_10f2b0e8-b00` (term-sheet negotiation, real Telegram `owner_reply` from Link side, `source: telegram_inbound`). Wrapper smoke then hit Cloudflare Durable Objects free-tier quota; follow-up found stale local bridges converting relay errors into empty polls, so `bridge.mjs` now exits on auth/not-found and backs off on quota/server errors. Added Lessons AO-AQ and redacted artifacts.
- **2026-04-17** Hosted relay hardening and BYO deploy path. Added create-key admission control, create kill switches, room TTL/message/text/heartbeat caps, `clawroomctl`/E2E create-key support, and `skills/deploy-clawroom-relay/SKILL.md` for agent-friendly BYO relay deployment. Added Lesson AR.
- **2026-04-17** BYO relay tunnel E2E. Room `t_1f97d969-595` used local `wrangler dev` relay exposed by ngrok while Railway Link joined as the guest, reached mutual close, and stopped both runtimes. Cloudflare Quick Tunnel failed locally with edge TLS EOFs, so ngrok is the current tunnel escape hatch. Added Lesson AS and the self-validating redacted artifact.
- **2026-04-18** Hosted relay quota recovered and create-key gating was deployed to production. No-key create now returns `401 create_key_required`; authenticated create succeeds. Two Telegram self-launch smoke attempts (`t_5d82f11e-e4d`, `t_f4454ea3-924`) failed before negotiation because the host bridge timed out on the opening OpenClaw call. Reinstalled the local OpenClaw LaunchAgent to the current CLI, added bridge `v3.1.1` async gateway event handling, extended/configured agent timeout, wrote fatal `failed` relay heartbeats, refreshed the gist bundle, and verified with local bridge smoke room `t_1f9480ad-721`. Follow-up real Telegram hosted smoke `t_efa33869-432` passed with both runtimes stopped. Added Lesson AT plus failed and passing redacted artifacts.
- **2026-04-19** H1 strict T3 average-user failure. Room `t_1651a049-2f9` proved a transient owner-reply fetch failure plus a normal non-reply recovery message can bypass `owner_reply` and close above mandate. Added retry/idempotent success handling in Telegram inbound, added non-reply single-binding owner-reply recovery, patched `JPY 75,000` parsing, and committed the failed artifact plus screenshots as Lesson AU.
- **2026-04-19** H1 recovery verification plus safe launcher-output regression. Room `t_aa6c678f-12f` passed strict T3 with real Telegram inbound owner approval (`source: telegram_inbound`). Added `launcher.mjs --owner-facing`, refreshed the gist launcher, and reran average calendar (`t_d3367a68-dd6`) plus 8-turn term-sheet (`t_08592cec-253`) checks. Both passed validator and have screenshot-backed artifacts. Added Lesson AV and progress report [`docs/progress/STABILITY_E2E_RUNS_2026-04-19.md`](progress/STABILITY_E2E_RUNS_2026-04-19.md).
- **2026-04-19** Natural-language product-path public invite gate. Room `t_423bc8e2-d37` proved local clawd can create a room through the installed v3 skill and return a public invite, but Railway Link treated the public invite as main-agent chat instead of launching the v3 verified bridge. Screenshot review also caught the invite rendering as a `CR-...json` download card. Added failed redacted artifact, screenshot evidence, and Lesson AW.
- **2026-04-19** Product-path visible-skill verification. Installed the current v3 skill into Railway Link's OpenClaw-visible `/data/workspace/skills/clawroom-v3` path and synced local clawd's visible skill. Room `t_71abe35b-cd9` then passed natural-language local clawd create -> public invite -> Railway Link join -> mutual close, with both runtimes stopped and a human invite preview. Added Lesson AX, the passing redacted artifact, screenshot evidence, and future-agent runbook [`docs/runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md`](runbooks/CLAWROOM_V3_E2E_AND_DEBUG.md).
- **2026-04-19** Product-path strict T3 bidirectional owner-reply gate. Room `t_fbc2bcd0-57e` failed semantically because guest accepted below Tom's `JPY 75,000` floor despite transport success. Added guest floor mandate parsing, skill owner-context guidance, and validator `guest_floor_compliance`; room `t_ebfeb7da-0b6` then passed with two real Telegram inbound owner replies, final `JPY 75,000`, mutual close, and both runtimes stopped. Added Lesson AY plus failed and passing redacted artifacts.
- **2026-04-20** ASK_OWNER copy cleanup gate. Hid `Room`/`Role` labels behind `CLAWROOM_DEBUG_OWNER_REPLY`, synced the bridge into local clawd and Railway Link visible skill paths, and ran product-path room `t_f6997679-d1b`. Validator passed with real `telegram_inbound` owner approval, final `JPY 75,000`, mutual close, and screenshot evidence showing no runtime labels or launcher details in the fresh ASK_OWNER prompt. Added Lesson AZ.
- **2026-04-20** Product-path hard gates continued. Room `t_5edced11-e61` failed H4 because the close summary omitted a next step; `bridge.mjs` now keeps the goal in every turn and requires an owner-ready close summary. Rerun `t_c3baf829-11c` passed 8-turn H4 with 3 real Telegram inbound owner replies. Non-reply recovery room `t_11cd6ca3-5e7` failed because the running Railway OpenClaw package lacked the fallback patch; after verifying and hotpatching the actual packaged Telegram bundle, adding launch/invite guard tests, and clearing stale bindings, clean room `t_73240be6-5b6` passed with a plain non-reply owner approval from Telegram. Added Lessons BA-BD plus the clean redacted artifact and screenshot evidence.
- **2026-04-20** Product boundary correction. Reclassified OpenClaw/Clawdbot source changes as optional adapter work, not ClawRoom release requirements. Added relay-owned owner decision URLs, changed the launcher feature gate to `owner-reply-url`, disabled Telegram inbound binding writes by default, and documented `owner_reply.source: owner_url` as the portable ASK_OWNER path. Added Lesson BE.
- **2026-04-20** Portable owner-url ASK_OWNER E2E passed. Room `t_34182ff8-eba` used normal Telegram product paths with local clawd and Railway Link, recorded both owner decisions as `source: owner_url`, closed at `JPY 75,000`, and stopped both runtimes. The first Link invite attempt hit an OpenClaw `payloads=0` incomplete turn, then a shorter ordinary retry succeeded. Added Lesson BF plus the self-contained redacted artifact and screenshot evidence.
- **2026-04-20** Clean-reinstall owner-url release-candidate H1 passed. Archived stale local and Railway ClawRoom state, removed and reinstalled both visible `clawroom-v3` skill bundles, verified hashes/preflight, then ran room `t_5b9218cb-cb8` through normal Telegram product paths. Validator passed from the embedded redacted transcript with `owner_reply.source: owner_url`, local host PID `46247`, Railway guest PID `43895`, mutual close, stopped runtimes, and screenshot-reviewed owner-facing output. Added Lesson BG plus the self-contained redacted artifact.
- **2026-04-20** Post-clean H4 rerun exposed a UI misroute. Room `t_f6d18ff9-c54` produced a good 8-message term-sheet transcript but failed the cross-machine oracle because the guest invite was accidentally sent to clawd, launching both roles locally. Added failed artifact and Lesson BH.
- **2026-04-20** Post-clean H4 direct runtime rerun passed. Room `t_4b919672-44d` used direct installed runtime commands instead of Telegram UI launch, then passed 8-message H4 with local host + Railway guest, owner_url approval, complete term sheet, mutual close, and both runtimes stopped. Added Lesson BI and redacted artifact.
- **2026-04-20** Telegram computer-use harness hardened. Added a fail-closed pre-send target guard to `scripts/telegram_e2e.mjs`: open target, screenshot, crop active Telegram title, OCR, verify expected chat title, and only then paste/send. Added `--target-check-only`, screenshot/title-crop evidence, runbook guidance, and Lesson BJ. Positive and negative target-guard checks passed locally.
- **2026-04-24** Install-path handoff clarified. `npx skills add heyzgj/clawroom --list` now finds exactly one skill, `clawroom-v3`, so `npx skills add heyzgj/clawroom --skill clawroom-v3` is the next public-install candidate to validate. It is not yet promoted to the release path until a disposable install proves the payload includes the four product runtime files without confusing the user's OpenClaw with maintainer-only docs, relay code, screenshots, scripts, or artifacts. Added [`docs/progress/NEXT_CHAT_HANDOFF_2026-04-24.md`](progress/NEXT_CHAT_HANDOFF_2026-04-24.md) and updated [`docs/design/install-path-v3-DRAFT.md`](design/install-path-v3-DRAFT.md).
