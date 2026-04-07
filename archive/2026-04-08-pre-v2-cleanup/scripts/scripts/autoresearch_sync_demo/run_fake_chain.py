from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.autoresearch_sync_demo.common import api_base_url, http_json
from scripts.autoresearch_sync_demo.orchestrator import (
    apply_assignment_to_program,
    build_carry_forward_summary,
    build_room_payload,
    validate_sync_fields,
)


CYCLE_FIXTURES: list[dict[str, Any]] = [
    {
        "a1_summary": (
            "Cycle 1 summary for agent A1\n\n"
            "- Explored learning-rate and warmup changes over the last 5 experiments.\n"
            "- Best run stayed stable near lr=6e-4 with short warmup.\n"
            "- lr>=1e-3 diverged consistently.\n"
            "- Keeping heads fixed at 8 looked safer than changing attention width."
        ),
        "a2_summary": (
            "Cycle 1 summary for agent A2\n\n"
            "- Explored dropout and weight-decay combinations over the last 5 experiments.\n"
            "- Best run improved slightly with dropout around 0.1 and moderate weight decay.\n"
            "- batch_size>=128 looked worse on this setup.\n"
            "- Changing too many regularization knobs at once made the signal noisy."
        ),
        "a1_message": (
            "Phase 1 sync from A1: explored lr and warmup near 6e-4; best basin is lr 6e-4, warmup 10, "
            "heads 8, dropout 0.1 at val_bpb 0.9412, commit abc123; dead ends are lr >= 1e-3 and "
            "batch_size >= 128; promising next step is optimizer-only refinement around the current basin."
        ),
        "a2_message": (
            "Phase 1 sync from A2: explored dropout and weight decay plus minor architecture nudges; current "
            "best basin is still compatible with lr 6e-4, heads 8, dropout 0.1; dead ends I accept are "
            "lr >= 1e-3, batch_size >= 128, and multi-knob changes add too much noise; proposed split is "
            "A1 optimizer-only refinement, A2 regularization-only refinement."
        ),
        "fields": {
            "best_result_summary": "Shared best basin: val_bpb 0.9412 around lr 6e-4, warmup 10, heads 8, dropout 0.1, commit abc123.",
            "dead_ends_summary": "lr >= 1e-3 diverges; batch_size >= 128 regresses; multi-knob changes add noise.",
            "assignment_a1": "focus: optimizer-only refinement around lr 4e-4 to 8e-4 with warmup 5 to 20; constraints: keep heads 8 and dropout 0.1 fixed.",
            "assignment_a2": "focus: regularization-only refinement over dropout and weight decay near the current basin; constraints: keep lr 6e-4 and heads 8 fixed.",
        },
    },
    {
        "a1_summary": (
            "Cycle 2 summary for agent A1\n\n"
            "- Followed the optimizer-only assignment.\n"
            "- Narrow lr and warmup sweeps improved the basin slightly.\n"
            "- Best local run landed around lr=5.8e-4 with warmup in the 8-12 range.\n"
            "- warmup=0 remained unstable and is likely another dead end."
        ),
        "a2_summary": (
            "Cycle 2 summary for agent A2\n\n"
            "- Followed the regularization-only assignment.\n"
            "- Dropout around 0.08 to 0.10 helped a bit, with moderate weight decay.\n"
            "- dropout=0.2 clearly hurt quality.\n"
            "- Regularization helped, but did not beat the tighter optimizer basin from A1."
        ),
        "a1_message": (
            "Phase 1 sync from A1: cycle 2 stayed inside the optimizer lane and improved the best basin to "
            "about val_bpb 0.9398 around lr 5.8e-4 with warmup 8-12; warmup 0 still looks unstable and "
            "should join the dead-end list."
        ),
        "a2_message": (
            "Phase 1 sync from A2: cycle 2 stayed inside the regularization lane; dropout around 0.08-0.10 "
            "with moderate weight decay was best on my side, but dropout 0.2 clearly regressed; I agree the "
            "global best now comes from A1, and the next split should stay non-overlapping with A1 refining "
            "warmup windows and A2 narrowing weight decay."
        ),
        "fields": {
            "best_result_summary": "Shared best basin: val_bpb 0.9398 around lr 5.8e-4, warmup 8-12, heads 8, dropout 0.08-0.1, commit def456.",
            "dead_ends_summary": "lr >= 1e-3 diverges; batch_size >= 128 regresses; warmup = 0 destabilizes; dropout = 0.2 regresses.",
            "assignment_a1": "focus: refine warmup and narrow lr around 5.5e-4 to 6.2e-4; constraints: keep heads 8 and dropout 0.08 to 0.1 fixed.",
            "assignment_a2": "focus: narrow weight decay and dropout around the current basin; constraints: keep lr 5.8e-4 and heads 8 fixed.",
        },
    },
    {
        "a1_summary": (
            "Cycle 3 summary for agent A1\n\n"
            "- Followed the narrow optimizer refinement assignment.\n"
            "- Best local run improved again around lr=5.7e-4 with warmup=9.\n"
            "- Longer warmup above 16 no longer helped.\n"
            "- This lane now looks close to saturation."
        ),
        "a2_summary": (
            "Cycle 3 summary for agent A2\n\n"
            "- Followed the narrow regularization assignment.\n"
            "- Best regularization setting converged near dropout 0.08 and moderate weight decay.\n"
            "- Aggressive weight decay clearly regressed.\n"
            "- My lane confirmed the basin but still did not beat A1's best run."
        ),
        "a1_message": (
            "Phase 1 sync from A1: cycle 3 improved the global best again to about val_bpb 0.9389 around "
            "lr 5.7e-4 with warmup 9; long warmup above 16 looks exhausted and should be treated as a dead end."
        ),
        "a2_message": (
            "Phase 1 sync from A2: cycle 3 confirms the same basin from the regularization side; aggressive "
            "weight decay regressed, and the strongest split going forward is to keep A1 on tiny optimizer "
            "refinement while A2 validates neighboring regularization settings without touching lr."
        ),
        "fields": {
            "best_result_summary": "Shared best basin: val_bpb 0.9389 around lr 5.7e-4, warmup 9, heads 8, dropout 0.08, commit ghi789.",
            "dead_ends_summary": "lr >= 1e-3 diverges; batch_size >= 128 regresses; warmup = 0 destabilizes; warmup > 16 is wasteful; aggressive weight decay regresses.",
            "assignment_a1": "focus: tiny optimizer-only refinement around lr 5.6e-4 to 5.9e-4 and warmup 8 to 10; constraints: keep architecture and regularization fixed.",
            "assignment_a2": "focus: validate nearby dropout and moderate weight decay settings around the current basin; constraints: do not change lr or warmup.",
        },
    },
]


def _seed_program(path: Path) -> None:
    if path.exists():
        return
    path.write_text(
        "# Autoresearch Program\n\n"
        "Make one small, testable change per experiment. Keep notes crisp. Prefer disciplined exploration over noisy thrash.\n"
    )


def _create_room(
    *,
    base_url: str,
    cycle: int,
    parent_room_id: str | None,
    prior_outcome_summary: str | None,
) -> dict[str, Any]:
    args = SimpleNamespace(
        topic=f"autoresearch sync - fake chain cycle {cycle}",
        goal="Share findings, mark dead ends, and split the next exploration direction without duplicate work.",
        participants=["agent_a1", "agent_a2"],
        required_field=[],
        turn_limit=12,
        timeout_minutes=30,
        stall_limit=3,
        min_turns=4,
        min_unique_participants=2,
        parent_room_id=parent_room_id,
        prior_outcome_summary=prior_outcome_summary,
        prior_outcome_refs=None,
    )
    return http_json("POST", f"{api_base_url(base_url)}/rooms", payload=build_room_payload(args))


def _join(base_url: str, room_id: str, token: str, client_name: str, summary: str) -> None:
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/join",
        token=token,
        payload={"client_name": client_name, "context_envelope": {"summary": summary, "refs": []}},
    )


def _send(base_url: str, room_id: str, token: str, *, intent: str, text: str, fills: dict[str, str] | None = None, expect_reply: bool = False) -> None:
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token,
        payload={
            "intent": intent,
            "text": text,
            "fills": fills or {},
            "facts": [],
            "questions": [],
            "expect_reply": expect_reply,
            "meta": {},
        },
    )


def _wait_closed(base_url: str, room_id: str, host_token: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] | None = None
    while time.time() < deadline:
        last = http_json("GET", f"{api_base_url(base_url)}/rooms/{room_id}", host_token=host_token)
        room = last.get("room") or {}
        if room.get("status") == "closed":
            return last
        time.sleep(0.1)
    raise RuntimeError(f"room {room_id} did not close in time: {json.dumps(last or {}, ensure_ascii=False)}")


def _result(base_url: str, room_id: str, host_token: str) -> dict[str, Any]:
    return http_json("GET", f"{api_base_url(base_url)}/rooms/{room_id}/monitor/result?host_token={host_token}")


def _apply_assignments(workspace_root: Path, fields: dict[str, Any]) -> dict[str, str]:
    a1_path = workspace_root / "a1/program.md"
    a2_path = workspace_root / "a2/program.md"
    apply_assignment_to_program(a1_path, fields, "assignment_a1")
    apply_assignment_to_program(a2_path, fields, "assignment_a2")
    return {"assignment_a1": str(a1_path), "assignment_a2": str(a2_path)}


def run_chain(*, base_url: str, workspace_root: Path, cycles: int) -> dict[str, Any]:
    if cycles < 1:
        raise ValueError("cycles must be >= 1")
    if cycles > len(CYCLE_FIXTURES):
        raise ValueError(f"cycles cannot exceed {len(CYCLE_FIXTURES)} with current fixtures")

    state_dir = workspace_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (workspace_root / "a1").mkdir(parents=True, exist_ok=True)
    (workspace_root / "a2").mkdir(parents=True, exist_ok=True)
    _seed_program(workspace_root / "a1/program.md")
    _seed_program(workspace_root / "a2/program.md")

    cycle_outputs: list[dict[str, Any]] = []
    parent_room_id: str | None = None
    prior_outcome_summary: str | None = None

    for idx in range(cycles):
        cycle_no = idx + 1
        fixture = CYCLE_FIXTURES[idx]
        created = _create_room(
            base_url=base_url,
            cycle=cycle_no,
            parent_room_id=parent_room_id,
            prior_outcome_summary=prior_outcome_summary,
        )
        room = created["room"]
        room_id = room["id"]
        host_token = created["host_token"]
        token_a1 = created["invites"]["agent_a1"]
        token_a2 = created["invites"]["agent_a2"]

        _join(base_url, room_id, token_a1, "a1-sync", fixture["a1_summary"])
        _join(base_url, room_id, token_a2, "a2-sync", fixture["a2_summary"])
        _send(base_url, room_id, token_a1, intent="NOTE", text=fixture["a1_message"], expect_reply=True)
        _send(base_url, room_id, token_a2, intent="ANSWER", text=fixture["a2_message"])
        _send(
            base_url,
            room_id,
            token_a1,
            intent="ANSWER",
            text="Shared state is aligned. Locking the next-cycle split and shared dead ends now.",
            fills=fixture["fields"],
        )
        _send(base_url, room_id, token_a1, intent="DONE", text="DONE")
        _send(base_url, room_id, token_a2, intent="DONE", text="DONE")

        closed = _wait_closed(base_url, room_id, host_token)
        result_payload = _result(base_url, room_id, host_token)
        result = result_payload["result"]
        fields = result["fields"]
        issues = validate_sync_fields(fields)
        if issues:
            raise RuntimeError(f"cycle {cycle_no} produced weak outcome: {'; '.join(issues)}")
        applied = _apply_assignments(workspace_root, fields)

        cycle_record = {
            "cycle": cycle_no,
            "room_id": room_id,
            "parent_room_id": room["parent_room_id"],
            "prior_outcome_summary": room["prior_outcome_summary"],
            "status": result["status"],
            "stop_reason": result["stop_reason"],
            "turn_count": result["turn_count"],
            "fields": fields,
            "applied": applied,
            "closed_room": closed["room"],
        }
        cycle_outputs.append(cycle_record)
        (state_dir / f"cycle_{cycle_no}_result.json").write_text(json.dumps(cycle_record, ensure_ascii=False, indent=2))

        parent_room_id = room_id
        prior_outcome_summary = build_carry_forward_summary(fields)

    return {
        "workspace_root": str(workspace_root),
        "cycles": cycle_outputs,
        "final_programs": {
            "a1": str(workspace_root / "a1/program.md"),
            "a2": str(workspace_root / "a2/program.md"),
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an automated multi-cycle fake ClawRoom sync chain for preflight testing.")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--cycles", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    out = run_chain(base_url=args.base_url or api_base_url(None), workspace_root=Path(args.workspace_root), cycles=int(args.cycles))
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
