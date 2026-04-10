# Pre-publish checklist

Last updated: 2026-04-09

## What's ready

- [x] `api.clawroom.cc` Cloudflare Worker — deployed, healthy, `/act/create` + cancel URL + mutual_done all validated
- [x] `clawroom.cc` Cloudflare Pages — deployed, `skill.md` serves v2.2.0 (270 lines)
- [x] `npx skills add heyzgj/clawroom` — installs 16-file bundle, confirmed on 3 real bots (KK/clawd/Link)
- [x] SKILL.md v2.2.0 — open immediately, status shape, fills-every-send, GET-only, cancel URL, ASK_OWNER
- [x] Codebase cleanup — `archive/` has all old Python; top-level is just `apps/{edge,monitor}` + `.agents/skills/clawroom/` + `docs/`
- [x] README.md / CLAUDE.md / INSTALL_SKILL.md — all current
- [x] LESSONS_LEARNED.md — comprehensive, includes 2026-04-08 hardcore E2E section
- [x] E2E validated:
  - 5/5 Telegram-pair pass (A1-tg, A2-tg, B1-tg, C1-tg, D1-tg)
  - 1/1 cross-platform pass (Feishu KK × Telegram clawd)
  - 3/3 subagent-simulated pass (R1b goal_done, R2b privacy 4/4, R3 ASK_OWNER)
  - 1/1 dedup script pass (host_start_room.py)

## Must fix before publish

- [ ] **`room_poller.py` network resilience** — add retry/reconnect + exponential backoff on transient errors. Currently crashes silently on network blips. (fix in progress)
- [ ] **`render_guest_joined.py` JSON schema mismatch** — expects older launch payload shape. (fix in progress)
- [ ] **Model quota strategy** — MiniMax-M2.7 daily quota exhausted after ~3-4 rooms. Decide: upgrade quota, switch model, or document the constraint. This is a user-facing issue for anyone running OpenClaw on MiniMax.

## Should fix before publish

- [ ] Monitor link exposes `host_token` in URL query string — contradicts SKILL.md's "don't show tokens" rule. Options: signed short URL, hash fragment, or accept the UX tradeoff and document it.
- [ ] SKILL.md should suggest absolute field names (`bamboo_work`, `nimbus_work`) instead of relative ones (`our_work`, `their_work`) to avoid last-writer-wins confusion.
- [ ] Add a "How to uninstall" section to INSTALL_SKILL.md and clawroom.cc landing.

## Nice to have (can ship without)

- [ ] Defensive regex strip for model-provider chain-of-thought tag leaks (e.g. `</think_never_used_...>` from MiniMax)
- [ ] Make SKILL.md's clarifying-question threshold more explicit ("act if 80%+ sure, else ask one question") to reduce bot-to-bot UX variation
- [ ] `host_start_room.py` auto-launches `room_poller.py` as a detached subprocess (instead of just printing the command)
- [ ] Blog post: "How we tested ClawRoom with real Telegram bots and what broke"

## Launch sequence (once must-fix are done)

1. Commit all fixes to `main`, push
2. `npx skills add heyzgj/clawroom --yes` on reference bots (force refresh)
3. Rebuild + deploy monitor: `cd apps/monitor && npm run build && npx wrangler pages deploy ./dist --project-name=clawroom-monitor --branch=main`
4. Verify: `curl -s https://clawroom.cc/skill.md | head -5` shows v2.2.x
5. Verify: `curl -s https://api.clawroom.cc/act/create?topic=smoke&goal=test&fields=x&timeout=5` returns a room
6. One final A1-style E2E run on the reference bots to confirm the poller fix holds
7. Publish: tweet / post / share the install link
