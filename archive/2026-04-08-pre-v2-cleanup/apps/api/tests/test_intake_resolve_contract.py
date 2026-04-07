from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_edge_worker_exposes_public_intake_resolve_route() -> None:
    source = (ROOT / "apps/edge/src/worker.ts").read_text(encoding="utf-8")
    assert 'url.pathname === "/intake/resolve"' in source
    assert "handleIntakeResolve" in source


def test_edge_worker_has_owner_request_validation() -> None:
    source = (ROOT / "apps/edge/src/worker_intake.ts").read_text(encoding="utf-8")
    assert 'badRequest("owner_request required")' in source
    assert 'status: blockers.length === 0 ? "ready" : "input_required"' in source
    assert "const counterpartSlot = inferCounterpartSlot(combined, counterpartHint);" in source
    assert 'participants: ["host", counterpartSlot]' in source
    assert "counterpart_slot: counterpartSlot" in source
    assert "do_not_copy_verbatim: true" in source
    assert 'one_question_mode: guidance ? "example_only" : null' in source
