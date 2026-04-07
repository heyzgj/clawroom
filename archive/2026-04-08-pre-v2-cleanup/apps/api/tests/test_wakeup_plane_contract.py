from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_edge_worker_declares_agent_inbox_binding_and_authenticated_route() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")
    wrangler = (ROOT / "apps" / "edge" / "wrangler.toml").read_text(encoding="utf-8")

    assert 'import { AgentInboxDurableObject } from "./worker_inbox";' in source
    assert "AGENT_INBOXES: DurableObjectNamespace;" in source
    assert 'const agentInboxMatch = url.pathname.match(/^\\/agents\\/([^/]+)\\/inbox$/);' in source
    assert "requireAgentInboxAuth" in source
    assert 'missing inbox bearer token' in source
    assert 'verify_inbox_token' in source
    assert 'new URL(`https://inbox/events${url.search}`)' in source
    assert 'name = "AGENT_INBOXES"' in wrangler
    assert 'class_name = "AgentInboxDurableObject"' in wrangler
    assert 'tag = "v4"' in wrangler


def test_agent_inbox_do_uses_epoch_retention_and_long_poll() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_inbox.ts").read_text(encoding="utf-8")

    assert "created_at_ms INTEGER NOT NULL" in source
    assert "DELETE FROM inbox_events WHERE created_at_ms < ?" in source
    assert "MAX_WAIT_SECONDS = 30" in source
    assert 'type must be \'room_invite\' or \'owner_gate_notification\'' in source
    assert "payload must be an object" in source
    assert "next_cursor" in source


def test_team_registry_persists_and_verifies_inbox_token_digest() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_teams.ts").read_text(encoding="utf-8")

    assert "inbox_token_digest TEXT NOT NULL DEFAULT ''" in source
    assert "managed_runnerd_url TEXT NOT NULL DEFAULT ''" in source
    assert 'String(body.inbox_token || "").trim()' in source
    assert 'String(body.managed_runnerd_url || "").trim()' in source
    assert "body.issue_inbox_token === true" in source
    assert "handleVerifyInboxToken" in source
    assert '.toArray()[0] as { inbox_token_digest?: string } | null' in source
    assert 'decodeURIComponent(verifyInboxMatch[1])' in source
    assert 'decodeURIComponent(internalAgentMatch[1])' in source
    assert 'token is required' in source
    assert 'invalid inbox token' in source
    assert 'inbox_token: inboxToken' in source
    assert "MONITOR_ADMIN_TOKEN?: string;" in source
    assert "hasValidMonitorToken" in source
    assert "monitor admin token required to issue inbox token" in source
    assert "monitor admin token required to bootstrap inbox token" in source
    assert "monitor admin token required to rotate inbox token" in source
    assert 'managed_runnerd_url: String(agent.managed_runnerd_url || "")' in source


def test_room_create_invite_fanout_uses_participant_mapping_and_top_level_links() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")

    assert "const participants = Array.isArray(body.participants)" in source
    assert 'const createdByAgentId = String(body.created_by_agent_id || "").trim();' in source
    assert 'const participantOwnerContexts = body.participant_owner_contexts' in source
    assert "const invites = responseBody.invites" in source
    assert "const joinLinks = responseBody.join_links" in source
    assert 'const joinLink = rawJoinLink.startsWith("/")' in source
    assert '`${url.origin}${rawJoinLink}`' in source
    assert "invite_token: inviteToken" in source
    assert "join_link: joinLink" in source
    assert 'const managedRunnerdUrl = typeof lookedUpAgent.managed_runnerd_url === "string"' in source
    assert 'managed_runnerd_url: managedRunnerdUrl' in source
    assert 'runtime: targetRuntime' in source
    assert 'const participantRuntimeHints: Record<string, { runtime: string; managed_runnerd_url: string }> = {};' in source
    assert 'responseBody.participant_runtime_hints = participantRuntimeHints;' in source
    assert "owner_context: typeof participantOwnerContexts[participant] === \"string\"" in source
    assert "inviteResults[participant] = \"agent_not_registered\"" in source
    assert "inviteResults[participant] = \"creator_direct\"" in source


def test_room_persists_participant_identity_and_fanouts_owner_gate_notifications() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")

    assert "AGENT_INBOXES?: DurableObjectNamespace;" in source
    assert "ALTER TABLE participants ADD COLUMN agent_id TEXT" in source
    assert "ALTER TABLE participants ADD COLUMN runtime TEXT" in source
    assert "ALTER TABLE participants ADD COLUMN display_name TEXT" in source
    assert "agent_id=COALESCE(?, agent_id)" in source
    assert "runtime=COALESCE(?, runtime)" in source
    assert "display_name=COALESCE(?, display_name)" in source
    assert 'await this.maybeWriteOwnerGateNotification(sender, msg.text, msg.meta || {}, roomId);' in source
    assert 'type: "owner_gate_notification"' in source or '"owner_gate_notification"' in source
    assert "owner_request_id" in source
    assert "agent_id: p.agent_id ? String(p.agent_id) : null" in source


def test_room_context_envelope_and_lineage_are_persisted_and_exposed() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")

    assert "parent_room_id TEXT" in source
    assert "prior_outcome_summary TEXT" in source
    assert "prior_outcome_refs_json TEXT NOT NULL DEFAULT '[]'" in source
    assert "outcome_contract_json TEXT NOT NULL DEFAULT '{}'" in source
    assert "field_mutation_version INTEGER NOT NULL DEFAULT 0" in source
    assert "context_envelope_json TEXT NOT NULL DEFAULT '{}'" in source
    assert "consensus_version INTEGER NOT NULL DEFAULT 0" in source
    assert "parent_room_id: parentRoomId" in source
    assert "prior_outcome_summary: priorOutcomeSummary || null" in source
    assert "prior_outcome_refs: priorOutcomeRefs" in source
    assert "outcome_contract: outcomeContract" in source
    assert "context_envelope: contextEnvelope" in source
    assert "const contextEnvelope = normalizeContextEnvelope(body?.context_envelope);" in source
    assert "context_envelope: normalizeContextEnvelope(this.parseJsonRecord(p.context_envelope_json))" in source


def test_edge_owner_gate_notifications_are_authenticated_and_shape_preserved() -> None:
    worker_source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")
    inbox_source = (ROOT / "apps" / "edge" / "src" / "worker_inbox.ts").read_text(encoding="utf-8")
    room_source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")

    assert 'const agentInboxMatch = url.pathname.match(/^\\/agents\\/([^/]+)\\/inbox$/);' in worker_source
    assert "requireAgentInboxAuth(request, env, agentId)" in worker_source
    assert 'new Request("https://inbox/events", {' in worker_source
    assert 'type must be \'room_invite\' or \'owner_gate_notification\'' in inbox_source
    assert 'type: "owner_gate_notification"' in room_source or '"owner_gate_notification"' in room_source
    assert 'await this.writeAgentInboxEvent(agentId, "owner_gate_notification", {' in room_source
    assert 'owner_request_id: ownerReqId || null' in room_source


def test_edge_owner_resolution_route_and_close_gate_alias_exist() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")

    assert 'tail === "/owner_resolution"' in source
    assert "handleOwnerResolution(request, roomId)" in source
    assert 'parts[2] === "close_gates"' in source
    assert "currentPendingCloseGate(roomId)" in source
    assert "body.action || body.decision" in source
