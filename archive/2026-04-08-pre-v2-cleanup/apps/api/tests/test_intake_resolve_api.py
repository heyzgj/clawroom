from __future__ import annotations


def test_intake_resolve_requires_one_clarify_before_ready(client) -> None:
    response = client.post(
        "/intake/resolve",
        json={"owner_request": "开个 ClawRoom，和另一个 OpenClaw 一起排下周内容。"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "input_required"
    assert body["ready_to_create"] is False
    assert body["missing_blockers"] == ["owner_confirmation"]
    assert body["clarify_guidance"]["do_not_copy_verbatim"] is True
    assert body["clarify_guidance"]["ask_for"] == ["confirmation"]
    assert "要我按这个形状开吗" in body["one_question"]
    assert body["one_question_mode"] == "example_only"
    assert body["draft_payload"]["required_fields"] == ["content_plan", "core_angles", "next_steps"]
    assert body["draft_payload"]["participants"] == ["host", "counterpart_openclaw"]
    assert body["inferred"]["counterpart_slot"] == "counterpart_openclaw"


def test_intake_resolve_becomes_ready_after_owner_reply(client) -> None:
    response = client.post(
        "/intake/resolve",
        json={
            "owner_request": "开个 ClawRoom，和另一个 OpenClaw 一起排下周内容。",
            "owner_reply": "可以，就按内容日历、核心角度和下一步来。",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["ready_to_create"] is True
    assert body["missing_blockers"] == []
    assert body["clarify_guidance"] is None
    assert body["one_question"] is None
    assert body["draft_payload"]["topic"] == "下周内容规划"


def test_intake_resolve_requests_source_material_for_cross_role_alignment(client) -> None:
    response = client.post(
        "/intake/resolve",
        json={"owner_request": "帮我开个房，让产品和运营先对齐这份需求文档。"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "input_required"
    assert body["missing_blockers"] == ["owner_confirmation", "source_material"]
    assert body["clarify_guidance"]["ask_for"] == ["confirmation", "source_material"]
    assert "需求文档" in body["one_question"]
    assert body["inferred"]["scenario_key"] == "cross_role_alignment"


def test_intake_resolve_uses_generic_fallback_shape(client) -> None:
    response = client.post(
        "/intake/resolve",
        json={"owner_request": "Open a ClawRoom to help me make a decision."},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft_payload"]["required_fields"] == ["decision", "rationale", "next_steps"]
    assert body["inferred"]["scenario_key"] == "generic_decision"


def test_intake_resolve_uses_counterpart_hint_as_participant_slot(client) -> None:
    response = client.post(
        "/intake/resolve",
        json={
            "owner_request": "Open a ClawRoom and invite another agent.",
            "counterpart_hint": "@singularitygz_bot",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["draft_payload"]["participants"] == ["host", "singularitygz_bot"]
    assert body["inferred"]["counterpart_slot"] == "singularitygz_bot"
