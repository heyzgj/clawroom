# Progress Tracker

## Milestones
- M0: Docs and traceability
- M1: Scaffolding and runtime baseline
- M2: Core API and store
- M3: Bridges
- M4: Test and evidence
- M5: Cloudflare edge backend

## Tasks
| Task ID | Milestone | Owner | Status | Acceptance |
|---|---|---|---|---|
| T-DOC-01 | M0 | Agent D | done | TC-DOC-01 |
| T-DOC-02 | M0 | Agent D | done | TC-DOC-02 |
| T-SCAF-01 | M1 | Agent A | done | TC-SCAF-01 |
| T-SCAF-02 | M1 | Agent A | done | TC-SCAF-02 |
| T-API-01 | M2 | Agent A | done | TC-API-01 |
| T-API-02 | M2 | Agent A | done | TC-API-02 |
| T-API-03 | M2 | Agent A | done | TC-RULE-01 |
| T-API-04 | M2 | Agent A | done | TC-RULE-02 |
| T-API-05 | M2 | Agent A | done | TC-RULE-03 |
| T-API-06 | M2 | Agent A | done | TC-OWNER-01 |
| T-API-07 | M2 | Agent A | done | TC-OWNER-02 |
| T-BRG-01 | M3 | Agent B | done | TC-E2E-01 |
| T-BRG-02 | M3 | Agent B | done | TC-E2E-02 |
| T-BRG-03 | M3 | Agent C | done | TC-CODEX-01 |
| T-QA-01 | M4 | Agent D | done | TC-E2E-01 |
| T-QA-02 | M4 | Agent D | done | TC-E2E-02 |
| T-QA-03 | M4 | Agent D | done | TC-VIS-01 |
| T-CF-01 | M5 | Agent A | done | TC-CF-01 |
| T-CF-02 | M5 | Agent A | done | TC-CF-02 |
| T-CF-03 | M5 | Agent A | done | TC-CF-03 |
| T-CF-04 | M5 | Agent B | done | TC-CF-04 |
| T-CF-05 | M5 | Agent D | done | TC-CF-05 |
| T-UI-02 | M5 | Design Agent | done | TC-UI-02 |

## Test Case Index
- TC-DOC-01: required docs exist
- TC-DOC-02: traceability links R -> T -> TC
- TC-SCAF-01: expected directories exist
- TC-SCAF-02: app imports resolve under uv run
- TC-API-01: create/join/leave flow
- TC-API-02: message and relay flow
- TC-RULE-01: required fields close room
- TC-RULE-02: mutual DONE close room
- TC-RULE-03: stall_limit close room
- TC-OWNER-01: ASK_OWNER does not close room
- TC-OWNER-02: OWNER_REPLY resumes and can close room
- TC-E2E-01: openclaw to openclaw core path
- TC-E2E-02: owner escalation path
- TC-CODEX-01: codex bridge smoke path
- TC-VIS-01: monitor visual timeline
- TC-CF-01: wrangler dev boots edge api
- TC-CF-02: core room endpoints on edge
- TC-CF-03: room TTL cleanup works
- TC-CF-04: openclaw bridge works with edge api
- TC-CF-05: cloudflare deploy runbook complete
- TC-UI-02: ClawRoom monitor wired to live events

## Notes
- Codex bridge smoke completed with `gpt-5-mini`.
- Edge backend verified locally; evidence: `reports/e2e_edge_result.json`.
- Cloudflare worker deployed: `https://api.clawroom.cc`.
- Cloudflare pages deployed: `https://686a5da9.clawroom-monitor.pages.dev`.
- Live OpenClaw to OpenClaw test against cloud API passed; evidence: `reports/clawroom_live_e2e_result.json`.
- Apex production domain live: `https://clawroom.cc`.
- Cloud domain repair + live API room verification evidence: `reports/cloudflare_domain_fix_2026-02-27.md`.
- Bridge networking hardened (`trust_env=False` + retry backoff) and cloud live OpenClaw E2E re-verified: `reports/live_openclaw_e2e_retry_20260227_180345/summary.json`.
- Onboarding CLI hardening completed (`join_url` workflow, monitor link derivation, command usability) and cloud smoke re-verified: `reports/cloud_openclaw_smoke_20260227_233206/summary.json`.
- Phase 2 channel work started (without outcome templates): owner channel abstraction now supports `--owner-channel openclaw`, `--owner-reply-cmd`, and fallback downgrade when `openclaw message read` is unsupported.
- New regression evidence for owner channel paths: `reports/e2e_owner_channel_smoke.json`.
- Live-domain gap check (2026-02-28): `clawroom.cc` / `api.clawroom.cc` still serve pre-Phase1/2 artifacts; latest local changes are not fully deployed yet.
- Skill package `skills/clawroom` added (plan-first onboarding + create/join/monitor flow).
- Publish and reference guide added: `docs/skills/CLAWROOM_ONBOARDING_SKILL_PUBLISH.md` (skills.sh + clawhub.ai paths).
