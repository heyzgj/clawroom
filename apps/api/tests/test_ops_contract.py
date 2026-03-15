from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_pytest_default_paths_include_conformance() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    testpaths = pyproject["tool"]["pytest"]["ini_options"]["testpaths"]
    assert "apps/api/tests" in testpaths
    assert "tests/conformance" in testpaths


def test_monitor_auth_fails_closed_when_token_missing() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")
    assert 'monitor admin token is not configured' in source
    assert '/monitor/summary' in source


def test_ops_ui_has_explicit_degraded_state() -> None:
    source = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert "function renderOpsDegraded" in source
    assert "degraded:" in source
    assert "Monitor admin token required. Open the ops link with ?admin_token=... first." in source
    assert "function resetOpsMetrics" in source
    assert "DOM.opsMetricTotal.textContent = '--';" in source


def test_registry_exposes_agent_friendly_summary_route() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_registry.ts").read_text(encoding="utf-8")
    assert 'url.pathname === "/monitor/summary"' in source
    assert "private renderSummaryText" in source
    assert 'const format = String(url.searchParams.get("format") || "json").toLowerCase();' in source
    assert "ROOM_LIST_CACHE_TTL_MS" in source
    assert "OVERVIEW_CACHE_TTL_MS" in source
    assert "private invalidateDerivedCaches()" in source
    assert "private shouldInvalidateDerivedCaches(" in source
    assert "this.roomListCache.set(cacheKey" in source
    assert "this.overviewCache.set(cacheKey" in source
    assert 'if (!message.includes("duplicate column name"))' in source
    assert 'SELECT updated_at FROM rooms WHERE status=\'active\' ORDER BY updated_at ASC LIMIT 1' in source
    assert '.toArray() as Record<string, unknown>[]' in source
    assert "SUM(CASE WHEN status='active' THEN participants_online ELSE 0 END) AS online_participants" in source
    assert "SUM(CASE WHEN status='active' THEN participants_joined ELSE 0 END) AS joined_participants" in source
    assert "SUM(CASE WHEN status='active' THEN active_runner_count ELSE 0 END) AS active_runners" in source
    assert "SUM(CASE WHEN execution_mode='compatibility' AND status='active' THEN 1 ELSE 0 END) AS compatibility_rooms" in source
    assert "SUM(CASE WHEN execution_mode!='compatibility' AND runner_certification='certified' AND status='active' THEN 1 ELSE 0 END) AS certified_managed_rooms" in source
    assert "SUM(CASE WHEN execution_mode!='compatibility' AND runner_certification='candidate' AND status='active' THEN 1 ELSE 0 END) AS candidate_managed_rooms" in source
    assert "SUM(CASE WHEN execution_mode!='compatibility' AND managed_coverage='full' AND status='active' THEN 1 ELSE 0 END) AS full_managed_rooms" in source
    assert "SUM(CASE WHEN execution_mode!='compatibility' AND managed_coverage='partial' AND status='active' THEN 1 ELSE 0 END) AS partial_managed_rooms" in source
    assert "SUM(CASE WHEN product_owned=1 AND status='active' THEN 1 ELSE 0 END) AS product_owned_rooms" in source
    assert "SUM(CASE WHEN automatic_recovery_eligible=1 AND status='active' THEN 1 ELSE 0 END) AS automatic_recovery_eligible_rooms" in source
    assert "SUM(CASE WHEN execution_mode='compatibility' AND active_runner_count <= 0 AND status='active' THEN 1 ELSE 0 END) AS unmanaged_compatibility_rooms" in source
    assert "SUM(CASE WHEN execution_attention_state='takeover_required' AND status='active' THEN 1 ELSE 0 END) AS takeover_required_rooms" in source
    assert "SUM(CASE WHEN status='active' THEN recovery_pending_count ELSE 0 END) AS recovery_pending_actions" in source
    assert "SUM(CASE WHEN status='active' THEN recovery_issued_count ELSE 0 END) AS recovery_issued_actions" in source
    assert "SUM(CASE WHEN status='active' AND (recovery_pending_count + recovery_issued_count) > 0 THEN 1 ELSE 0 END) AS recovery_backlog_rooms" in source
    assert "primary_root_cause_code" in source
    assert "primary_root_cause_confidence" in source
    assert "primary_root_cause_summary" in source
    assert "root_cause_hints_json" in source
    assert 'room.execution_attention_reasons.includes("repair_claim_overdue")' in source
    assert 'room.execution_attention_reasons.includes("owner_reply_overdue")' in source
    assert "repair_package_issued_rooms" in source
    assert "repair_claim_overdue_rooms" in source
    assert "owner_reply_overdue_rooms" in source
    assert "first_relay_risk_rooms" in source
    assert "runner_lease_low_rooms" in source
    assert "repair_issued_stale_seconds" in source
    assert "const rootCauseActiveRows = this.sql.exec(" in source
    assert "const rootCauseRecentRows = this.sql.exec(" in source
    assert "root_causes: {" in source
    assert 'key: "dominant_root_cause"' in source
    assert "root_causes: overview.root_causes" in source
    assert "root_causes: active_top=" in source
    assert "registry_cache:" in source
    assert 'key: "runner_attention"' in source
    assert 'key: "takeover_attention"' in source
    assert 'key: "recovery_backlog"' in source
    assert 'key: "repair_claim_overdue"' in source
    assert 'key: "owner_reply_overdue"' in source
    assert 'key: "first_relay_risk"' in source
    assert 'key: "runner_lease_low"' in source
    assert 'key: "compatibility_unmanaged"' in source
    assert 'key: "managed_uncertified"' in source
    assert "start_slo_ms:" in source
    assert "execution_mode: room.execution_mode" in source
    assert "runner_certification: room.runner_certification" in source
    assert "managed_coverage: room.managed_coverage" in source
    assert "product_owned: room.product_owned" in source
    assert "automatic_recovery_eligible: room.automatic_recovery_eligible" in source
    assert "attempt_status: room.attempt_status" in source
    assert "execution_attention_state: room.execution_attention_state" in source
    assert "primary_root_cause_code: room.primary_root_cause_code" in source
    assert "primary_root_cause_summary: room.primary_root_cause_summary" in source
    assert "takeover_required: room.takeover_required" in source
    assert "recovery_pending_count: room.recovery_pending_count" in source
    assert "recovery_issued_count: room.recovery_issued_count" in source


def test_ops_ui_surfaces_runner_plane_and_start_slo_summary() -> None:
    source = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert "function fmtDurationMs" in source
    assert "Runner Plane" in source
    assert "Root Causes" in source
    assert "Start SLO" in source
    assert "room(s) need runner attention" in source
    assert "room(s) need takeover" in source
    assert "room(s) carrying recovery backlog" in source
    assert "room(s) with overdue repair claims" in source
    assert "room(s) waiting too long for an owner reply" in source
    assert "Recovery backlog · pending" in source
    assert "execution_attention_summary" in source
    assert "Likely root cause" in source
    assert "Active top:" in source
    assert "execution_mode || 'compatibility'" in source
    assert "runner_certification || 'none'" in source
    assert "managedCoverage" in source
    assert "productOwned" in source
    assert "attempt_status || 'pending'" in source
    assert "product-owned room(s)" in source
    assert "fully managed room(s)" in source
    assert "partially managed room(s)" in source
    assert "pending recovery action(s)" in source
    assert "issued recovery action(s)" in source
    assert "room(s) with repair packages already sent" in source
    assert "room(s) with overdue repair claims" in source
    assert "room(s) with overdue owner replies" in source
    assert "room(s) at first-relay risk" in source
    assert "room(s) with a low runner lease" in source
    assert "Runner checkpoint" in source


def test_room_fetch_path_has_timeout_catch_up_close() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert "await this.closeExpiredRoomIfNeeded(roomId, { debounceMs: isHotReadPath ? HOT_PATH_EXPIRY_CHECK_DEBOUNCE_MS : 0 });" in source
    assert 'await this.closeRoom("timeout", "deadline exceeded");' in source


def test_worker_room_emits_root_cause_incident_logs() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert '"root_cause_hints_v1"' in source
    assert "private deriveRootCauseHints(" in source
    assert "private emitIncidentLog(" in source
    assert 'log_type: "clawroom_room_incident"' in source
    assert "primary_root_cause" in source
    assert "root_cause_hints: room.root_cause_hints.map" in source
    assert "supervision_origins: execution.supervisionOrigins" in source
    assert 'const CERTIFIED_SUPERVISION_ORIGINS = new Set(["runnerd", "direct"]);' in source


def test_worker_room_prepares_manual_repair_packages_after_grace() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert "MANUAL_REPAIR_PREPARE_SECONDS" in source
    assert "private manualRepairPrepareSeconds()" in source
    assert "private async maybePrepareManualRecoveryActions(" in source
    assert 'prepared_by_system: true' in source
    assert "await this.maybePrepareManualRecoveryActions(roomId, attemptRecords);" in source
