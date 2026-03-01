# Traceability Matrix

| Requirement | Design Source | Task | Test | Evidence |
|---|---|---|---|---|
| R-001 | PRD Functional Requirements | T-API-01 | TC-API-01 | tests output: reports/tc_pytest.txt |
| R-002 | PRD Functional Requirements | T-API-01 | TC-API-01 | tests output: reports/tc_pytest.txt |
| R-003 | PROTOCOL Message Object | T-API-02 | TC-API-02 | tests output: reports/tc_pytest.txt |
| R-004 | PROTOCOL expect_reply Semantics | T-API-02 | TC-API-02 | tests output: reports/tc_pytest.txt |
| R-005 | PROTOCOL Owner Loop | T-API-06 | TC-OWNER-01 | tests output: reports/tc_pytest.txt + logs: reports/e2e_owner.log |
| R-006 | PROTOCOL Owner Loop | T-API-07 | TC-OWNER-02 | tests output: reports/tc_pytest.txt + logs: reports/e2e_owner.log |
| R-007 | ARCH Stop Rule Ordering | T-API-03,T-API-04,T-API-05 | TC-RULE-01,TC-RULE-02,TC-RULE-03 | tests output: reports/tc_pytest.txt |
| R-008 | ARCH Event Model | T-API-02 | TC-VIS-01 | screenshot: reports/monitor.png |
| R-009 | PROTOCOL Result Object | T-API-01 | TC-API-01 | tests output: reports/tc_pytest.txt + json: reports/e2e_result.json |
| R-010 | OPENCLAW Bridge Loop | T-BRG-01,T-BRG-02 | TC-E2E-01,TC-E2E-02 | logs: reports/e2e_openclaw.log + reports/e2e_owner.log + reports/live_openclaw_e2e_retry_20260227_180345/summary.json + reports/cloud_openclaw_smoke_20260227_233206/summary.json |
| R-010 | CODEX Bridge Loop (optional) | T-BRG-03 | TC-CODEX-01 | logs: reports/codex_bridge_smoke.log + json: reports/codex_bridge_result.json |
| R-011 | ADR 0002 Cloudflare Edge Backend | T-CF-01,T-CF-02 | TC-CF-01,TC-CF-02 | json: reports/e2e_edge_result.json + reports/cloudflare_deploy.txt + reports/cloudflare_domain_fix_2026-02-27.md |
| R-012 | ADR 0002 Ephemeral TTL | T-CF-03 | TC-CF-03 | json: reports/e2e_edge_ttl.json |
| R-013 | DESIGN_AGENT_BRIEF ClawRoom Monitor Live Wiring | T-UI-02 | TC-UI-02 | walkthrough: /Users/supergeorge/.gemini/antigravity/brain/15a7f50f-7c7c-42d5-8bc7-f59f18e7ca38/walkthrough.md + screenshots: reports/monitor_empty_state.png, reports/monitor_connecting_state.png + pages deploy: reports/cloudflare_deploy.txt |

## Evidence Policy
1. Every milestone completion must produce log or screenshot evidence.
2. Evidence paths must be stored under `reports/`.
3. If evidence cannot be generated automatically, note reason and manual steps.
