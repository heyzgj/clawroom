# Context Handoff to Codex — 2026-04-15

You are reading this because the owner needs you to sync with the current
state of the ClawRoom project. This document is self-contained — do NOT
assume you remember what you did in past sessions unless the owner tells
you so explicitly.

## 0. Read these first (in order)

```
1. ~/Desktop/project/clawroom/CLAUDE.md           # current project rules
2. ~/Desktop/project/clawroom/MIGRATION.md        # 2026-04-15 migration record
3. ~/Desktop/project/clawroom/docs/LESSONS_LEARNED.md    # especially Parts 6-7 and lessons Z-AI
4. ~/Desktop/project/clawroom/docs/V3_1_E2E_REPORT.md    # the passing E2E writeup
```

Everything below is the synthesis. The files above are the source of
truth. If anything here contradicts those files, trust the files.

---

## 1. What ClawRoom is (one paragraph)

A protocol and reference implementation for two AI agents owned by two
different people to meet in a single-purpose bounded room, negotiate or
coordinate to a named outcome, and return a structured result plus a
natural-language summary to each owner. Owner-in-the-loop for any
authorization decision. Zero install required on the invited (guest)
side — the entire viral loop rides on one tokenized HTTP invite URL.

Differentiator (only product doing all four at once): **cross-owner +
structured terminal outcome + owner-in-the-loop + reliable bounded
close**.

---

## 2. Where we are today (2026-04-15)

**Canonical repo**: `~/Desktop/project/clawroom/`. Initial commit
`499be97`, root-commit, `main` branch, no remote yet.

**Architecture (v3.1)**:

```
 Owner A (Telegram)       Owner B (Telegram)
        │                         │
        ▼                         ▼
  OpenClaw host A         OpenClaw host B (Railway)
   + clawroom skill        + clawroom skill
        │                         │
  launcher.mjs              launcher.mjs
  → bridge.mjs (host)       → bridge.mjs (guest)
        │                         │
        └─────────┬───────────────┘
                  │ HTTP (long-poll)
                  ▼
        Cloudflare Worker Relay
        SQLite Durable Object per thread
        clawroom-v3-relay.heyzgj.workers.dev
```

**Relay** is a thin mailbox. Mechanical rules only: same-role 409 (turn
gate), `closed := host_closed ∧ guest_closed`, TTL. No semantic
interpretation.

**Bridge** is a zero-npm Node daemon per (thread, role). Long-polls
relay, talks to OpenClaw Gateway via WS client (not CLI — Lesson G1),
scans `REPLY:` / `CLAWROOM_CLOSE:` markers, notifies owner via direct
Telegram Bot API at close.

**Launcher** is a detached starter that waits for PID + runtime-state +
relay heartbeat + log path before claiming success (Lesson AF).

**Dedicated agent**: `clawroom-relay`, not `main`. Session key scheme
`agent:clawroom-relay:clawroom:<thread>:<role>` (Lesson AD).

**Owner notification**: direct Telegram Bot API `sendMessage`, never
OpenClaw `deliver` (Lesson F2 risks notification-as-instruction).

---

## 3. What happened between 2026-04-07 and 2026-04-15 (condensed timeline)

| Date | Event |
|---|---|
| 2026-04-07 | LESSONS_LEARNED v1 written — Parts 1-5, lessons A-Y. Thick-protocol v2 stack at state of the art. |
| 2026-04-08 | Commit `677b86a` "chore: archive pre-v2 Python stack, normalize repo to edge + monitor + skill". Moved `apps/{api,runnerd,openclaw-bridge}` into `archive/2026-04-08-pre-v2-cleanup/`. **Side effect** (not intended at the time): killed the certified "runnerd + room_poller + Telegram round-trip" path. |
| 2026-04-08/09 | Real Telegram E2E matrix attempted (Link × clawd × KK). Every run needed owner nudges. Misdiagnosed at the time as MiniMax timeout; actual root cause was "we removed the watcher". |
| 2026-04-10 | "Wow scenarios" S1-S3 run via **Claude Code subagent + curl** (not through real bots). All 3 deal-closed. Demonstrated the agent CAN do adversarial negotiation — did NOT prove the runtime plumbing works on real bots. |
| 2026-04-11 | Three commits attempting to fix the nudge problem via `cron.add` (`0b0fdd8`, `175545f`, `7d50cec`). Well-intentioned but architecturally a band-aid for the missing runnerd. |
| 2026-04-12 | Strategic rethink. Adopted "thin server + smart agent" principle: every failure in Part 1 came from the server trying to understand the conversation (intents, fills, continuation). Stop doing that. Let server be a mailbox. |
| 2026-04-13/14 | v3.1 built under `~/Desktop/project/clawroom-v3/`. DO relay, verified launcher, bridge, Railway ops fixes (OPENCLAW_STATE_DIR, gateway client id, dedicated agent workspace, Telegram owner binding). Four E2E iterations: `t_a3c4d16f-959`, `t_f252d5a3-048`, `t_5eac03b0-942`, then passing `t_92615621-4a8`. Part 7 and lessons Z-AH written. |
| 2026-04-14 evening | Commit `a0c3616` in agent-chat: Part 7 + Lessons Z-AH already lived in LESSONS_LEARNED.md; added Lesson AI for marker-scan tolerance, redacted artifact `v3_1_t_92615621-4a8.redacted.json`, and README pointer to `../clawroom-v3/`. |
| 2026-04-15 | Folder `clawroom-v3/` renamed to `clawroom/`. Migration executed: 6 docs copied from agent-chat (byte-verified), landing design + mockup moved under `docs/design/`, new top-level docs written (MIGRATION.md, CLAUDE.md v3-first, README.md v3-first). Initial commit `499be97` in clawroom. Project memory updated to record the migration. |

---

## 4. Decisions made (the ones you must not re-open)

1. **v3-first, not v2.** The agent-chat thick-protocol worker is not being
   maintained. Do not invest engineering in it.
2. **Thin server, smart agent.** The relay does NOT track fields, intents,
   continuation, or DONE semantics. Those concerns live in the bridge and
   in the LLM that the bridge wraps. Every attempt to move this back to
   the server will hit Lessons A-G.
3. **Webhook push to OpenClaw is ruled out.** Lesson H proved the gateway
   binds to loopback; external HTTP cannot reach it. Do not re-test
   unless you have new evidence that the deployment model has changed.
   Long-poll is the viable alternative.
4. **Bridge is launched BY OpenClaw, not by operator.** SSH is diagnostic
   only (Lessons AA, Z). Any claimed "passing E2E" must have been triggered
   by a Telegram prompt, not by `ssh ... node bridge.mjs`.
5. **Dedicated `clawroom-relay` agent, isolated sessions.** Main session
   contamination (Lesson G1) killed v2; do not repeat.
6. **Owner notifications via direct Telegram Bot API.** Never via OpenClaw
   `deliver` (Lesson F2 notification-as-instruction risk).
7. **Verified launcher, not `nohup &`.** PID + runtime-state file + relay
   heartbeat + log path — all four before success (Lesson AF).
8. **Validator is the release gate.** Telegram "looked successful" is not
   evidence. Machine facts from relay state + runtime state + validator
   output are (Lesson AG).
9. **Repo split.** `clawroom/` is canonical. `agent-chat/` is frozen
   maintenance-only (still serves `api.clawroom.cc` and `clawroom.cc` via
   Cloudflare but receives no new development).
10. **Infrastructure migration is deferred**, tied to T3 + multi-turn
    validation passing, not to this code migration. Specifically: GitHub
    rename `heyzgj/clawroom` → `heyzgj/clawroom-v2-archive`, and DNS
    repointing of `.cc` domains. Do NOT do these early.

---

## 5. Current validation status

| Capability | Proven? | Evidence |
|---|---|---|
| T1 — create + join | ✅ | room `t_92615621-4a8` |
| T5 — mutual_close | ✅ | same |
| Cross-machine (macOS × Railway Linux) | ✅ | host PID 61589, guest PID 250, both heartbeats stopped |
| Direct Telegram Bot API notification | ✅ | both owners' DMs delivered at close |
| T2 — multi-turn negotiation | ✅ transport/runtime | room `t_0b3602a9-e3b`, 8 negotiation messages + 2 closes |
| T3 — ASK_OWNER round-trip | ✅ v0 | room `t_fb3fda2d-563`, tokenized POST owner-reply path |
| T4 — webhook push | ruled out | Lesson H; replaced by long-poll |
| Mandate/authorization guard | ✅ v0 | `t_fb3fda2d-563` closed at `¥65,000`; `t_0b3602a9-e3b` now fails validator |

---

## 6. What the owner is asking you (codex) to do

Run the next round of E2E on the v3.1 stack. Two independent test objects:

### 6a. T3 — ASK_OWNER round-trip E2E

Status update: T3 v0 passed on room `t_fb3fda2d-563` after failed room
`t_1f72571a-3f4` exposed that mutating GET owner-reply URLs can be
consumed by link previews/placeholders. The passing path uses the
tokenized POST owner-reply API via the E2E harness. Telegram
reply-to-message inbound routing remains future OpenClaw integration.

Original scenario shape:

- **Host owner context**: "agree on a brand deal with Tom's agent, budget
  ceiling is ¥50k; if Tom proposes above that, ask me first."
- **Guest owner context**: "counterparty agent; you are authorized to
  propose up to ¥80k to secure the deal."
- **Expected chain**: host opens → guest proposes ¥60k → host bridge
  triggers Telegram Bot API notification to host owner asking "Tom wants
  ¥60k, OK?" → host owner replies (via the v3 owner-reply surface; if one
  doesn't exist, this test DOUBLES as a chance to build it cleanly) →
  host bridge observes reply → continues negotiation
  → eventually mutual_close.

**Pass criteria**:
- ≥1 ASK_OWNER → owner reply → resume cycle in the relay event log
- validator output green on all 9 checks (`room_closed`, `mutual_close`,
  `event_count`, `message_count`, `close_roles`, `turn_taking`,
  `runtime_stopped`, `summary_present`, `not_echo_loop`)
- host owner's Telegram DM contains both the ASK_OWNER prompt AND the
  final summary, no internal jargon leakage

**Specific traps to watch for**:
- **Lesson F2**: when the bridge pokes the host owner's Telegram to ask a
  question, the owner's main OpenClaw session may treat the poke as a
  new instruction and do something bizarre. Direct Bot API bypass helps
  but verify.
- **Lesson AI** (marker scan): the bridge detects ASK_OWNER (or whatever
  v3 equivalent) by scanning OpenClaw output for specific strings. If
  the LLM writes "I'll check with my owner" instead of `REPLY:` with an
  ASK marker, the bridge misses it. **Harden the scan per Lesson AI BEFORE
  running T3**: tolerant regex, unmatched-turn counter, conservative
  fallback.

### 6b. T2-full — multi-turn S1-class negotiation

One of the subagent wow scenarios (S1 term sheet, S2 brand deal, S3 comp)
run through the v3.1 stack end-to-end on real Telegram bots. 8+ turns,
non-trivial content, preferably bilingual to stress marker scan.

**Pass criteria**:
- ≥8 messages on the relay (not counting close events)
- validator green
- both owners' final DM summaries contain the canonical terms and no
  internal jargon (no "room_id", "token", "poller", "bridge", "runtime")

---

## 7. The artifact discipline (non-negotiable)

Every E2E run you perform produces THREE committed artifacts in
`clawroom/`, and the commit goes to `main`:

1. **Redacted JSON artifact** at
   `docs/progress/v3_1_<room_id>.redacted.json`.
   Pattern set by `v3_1_t_92615621-4a8.redacted.json`. Copy the original
   from `~/.clawroom-v3/e2e/<room_id>.json`, replace `host_token`,
   `guest_token`, `invite_url` token segment, and any Telegram chat id
   with `REDACTED`. Keep bot handles (they're public). Keep PIDs,
   timestamps, summary text, heartbeats, and a redacted `transcript`
   array with `id/from/kind/text/ts`. Add a `_redaction_notice` preamble
   and a `coverage_note` tail naming which T's this run proves.

2. **Lesson entry** in `docs/LESSONS_LEARNED.md`. Next free letter as of
   this handoff update: **AL**. Format per Z-AK: `### <letter>. <Title>` /
   `**What:**` / `**Source check:**` or `**Why preemptive:**` (if
   applicable) / `**Symptom:**` (if a bug) / `**Fix:**` / `**Lesson:**`.
   Keep it under 30 lines.

3. **Updates Log line** appended at the bottom of LESSONS_LEARNED.md.
   Format: `- **<YYYY-MM-DD>** <one-paragraph summary, room id, lesson
   letter, outcome>`.

Commit these three together with a conventional `docs(lessons):` or
`feat(bridge):` prefix. Use a HEREDOC-safe message (no fancy Unicode
dashes, no smart quotes — recent experience is that heredoc quoting
breaks on those).

### If the run fails, commit the failure artifact too.

Failure is data. Name it
`docs/progress/v3_1_<room_id>.failed.redacted.json` and write a lesson
explaining what broke. We want the full iteration trail, not a highlight
reel.

---

## 8. Hard rules distilled (from Z-AI)

Pin these in your context before executing any E2E:

- **OPENCLAW_STATE_DIR** (Lesson AB): Railway Link uses `/data/.openclaw`,
  NOT `$HOME/.openclaw`. Bridge/launcher must respect env var. Dedicated
  agent workspace at `/data/.openclaw/workspaces/clawroom-relay` must be
  writable on the persistent volume.
- **Gateway client id** (Lesson AC): must be exactly `gateway-client`.
  Anything else gets rejected at connect schema. Run local gateway smoke
  (bridge handshake → `/status` reply) before Telegram E2E.
- **Dedicated workspace writable** (Lesson AD): existence of agent in
  config is not sufficient; verify actual workspace directory permissions
  in each runtime.
- **Telegram notification** (Lesson AE): needs bot token + explicit
  owner chat id + redacted logs + idempotent send + clear skip/fail
  when target missing.
- **Verified launcher** (Lesson AF): four conditions (PID, runtime-state,
  relay heartbeat, log path) before success.
- **Oracle is state, not UX** (Lesson AG): validator output is the gate.
- **Marker scan tolerant** (Lesson AI): regex not exact match,
  unmatched-turn counter, conservative fallback. Already hardened before
  T2-full; keep it in place before T3.
- **SSH diagnostic only** (Lesson AA): if your "passing" E2E started the
  bridge via SSH, it does not count as a passing E2E.
- **railway run is local** (Lesson Z): runs locally with Railway env
  vars, not on the container.

---

## 9. Do NOT

- Reintroduce structured intents / fills / continuation hints /
  required-fields tracking on the relay. Those are v2 patterns and the
  reason every failure A-G happened.
- Replace long-poll with webhook push. Lesson H.
- Start bridges via `nohup &` or `pm2 start --detached` without full
  launcher verification.
- Touch `agent-chat/` beyond read-only reference. It is frozen.
- Commit `.env` files, `node_modules/`, `~/.clawroom-v3/e2e/*.json`
  (those are your working artifacts, NOT the committed evidence — you
  copy the redacted version into `clawroom/docs/progress/`).
- Publish an npm package, PyPI package, or any SDK. The HTTP invite URL
  is the protocol.
- Promote any infrastructure move (DNS, GitHub rename) without the
  owner's explicit green light.

---

## 10. Open questions (not blockers, worth flagging when relevant)

- Tolerant marker scan — T2-full passed with bilingual content after
  hardening, but no unmatched-marker fallback was observed yet.
- Telegram reply-to-message routing still needs OpenClaw inbound support.
  T3 v0 used tokenized POST from the harness, not Telegram reply routing.
- Multi-turn bilingual marker scan stress — LLMs love switching colon
  styles between `:` and `：` when code-switching EN/CN. Regex handles
  it; confirm on real run.
- Validator turn-taking check — does it correctly accept an ASK_OWNER
  gap where the host pauses but guest should not post during the pause?
  (Current validator rules may need refinement for T3 shape.)
- Production-scale turn gate 409 retry semantics — what does the bridge
  do when it tries to post and gets 409? Currently likely treats as
  transient and retries, but under heavy concurrency that could loop.

---

## 11. Minimum self-check before you start

Answer these to confirm you're oriented:

1. Which repo is canonical? (`~/Desktop/project/clawroom/`)
2. What's the last passing E2E room id? (`t_0b3602a9-e3b`; first smoke pass was `t_92615621-4a8`)
3. Where do E2E artifacts go? (`clawroom/docs/progress/`, redacted)
4. Which OpenClaw agent runs the bridge? (`clawroom-relay`, not `main`)
5. Why is there no webhook push? (Lesson H: gateway loopback-only)
6. What's the next free lesson letter? (AL)
7. What's the difference between "validator passed" and "Telegram looks
   good"? (The former is evidence; the latter is not — Lesson AG)

If you can answer all seven without looking, you're synced. If you can't,
re-read Section 0 before doing any work.

---

Owner contact: George (@singularitygz on Telegram). Primary work surface:
Telegram DMs. Secondary: this repo.

Good luck. Commit everything.
