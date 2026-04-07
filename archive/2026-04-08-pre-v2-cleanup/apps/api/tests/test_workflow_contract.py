from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_edge_worker_declares_room_participant_workflow_binding_and_routes() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")
    assert "ROOM_PARTICIPANT_WORKFLOW" in source
    assert 'AI?: unknown' in source
    assert "function roomParticipantWorkflowId(" in source
    assert 'url.pathname === "/workflows/room-participants" && request.method === "POST"' in source
    assert 'url.pathname.startsWith("/workflows/room-participants/")' in source
    assert 'room_id, participant, and participant_token are required' in source
    assert 'kind: "workflow_kickoff"' in source
    assert 'workflow_mode' in source
    assert 'responseBody.workflow_started = true' in source
    assert 'responseBody.workflow_mode = "conversation"' in source
    assert 'responseBody.joined === true && responseBody.workflow_mode === "conversation"' in source
    assert 'parts[1] === "events" && request.method === "POST"' in source
    assert 'event type is required' in source
    assert 'request.method === "POST" && match.forwardPath.endsWith("/messages")' in source
    assert 'type: "room-event"' in source
    assert 'kind: "room_message"' in source
    assert 'workflow_mode: "conversation"' in source
    assert 'inviteResults[participant] = inboxResponse.ok ? "invite_written" : "invite_failed"' in source


def test_edge_exports_cloudflare_workflow_class() -> None:
    worker_source = (ROOT / "apps" / "edge" / "src" / "worker.ts").read_text(encoding="utf-8")
    workflow_source = (ROOT / "apps" / "edge" / "src" / "workflow_room_participant.ts").read_text(encoding="utf-8")
    wrangler = (ROOT / "apps" / "edge" / "wrangler.toml").read_text(encoding="utf-8")

    assert 'export { RoomParticipantWorkflow };' in worker_source
    assert "class RoomParticipantWorkflow extends WorkflowEntrypoint" in workflow_source
    assert 'step.waitForEvent' in workflow_source
    assert "AI?: AiBinding" in workflow_source
    assert "participant_token: string;" in workflow_source
    assert "await this.fetchEvents(checkpoint)" in workflow_source
    assert "type WorkflowActionableEvent" in workflow_source
    assert 'kind: "owner_resume"' in workflow_source
    assert "latestActionableEvent(events: RoomEventRow[], participant: string)" in workflow_source
    assert "await this.callModel(nextCheckpoint, room, actionable)" in workflow_source
    assert "await this.postMessage(nextCheckpoint, reply)" in workflow_source
    assert 'terminal_coercion: ["intent->DONE"]' in workflow_source
    assert 'final_state: "running" | "done_sent" | "room_closed" | "step_cap_reached"' in workflow_source
    assert 'type: "room-event"' in workflow_source
    assert 'binding = "ROOM_PARTICIPANT_WORKFLOW"' in wrangler
    assert 'class_name = "RoomParticipantWorkflow"' in wrangler
    assert 'binding = "AI"' in wrangler


def test_join_gate_resolution_requires_host_authority_not_invite_holder() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    start = source.index("private async handleJoinGateResolve")
    end = source.index("private async handleRunnerClaim", start)
    snippet = source[start:end]

    assert "private async handleJoinGateResolve" in source
    assert "await this.requireHost(request);" in snippet
    assert "authenticateJoinInvite" not in snippet
    assert '"SELECT * FROM owner_gates WHERE gate_id=? AND gate_type=\'join_approve\' LIMIT 1"' in snippet
    assert 'return json({ participant: gate.participant, gate: updated, room });' in snippet
