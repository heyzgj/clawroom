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

from scripts.autoresearch_sync_demo.common import api_base_url, http_json, request_monitor_result
from scripts.autoresearch_sync_demo.orchestrator import (
    DEFAULT_REQUIRED_FIELDS,
    apply_assignment_to_program,
    build_room_payload,
)

BASELINES = [0.9412, 0.9406, 0.9399, 0.9393, 0.9388]


def ensure_program(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(
            "# Autoresearch Program\n\n"
            "Make one small, testable change per experiment. Keep notes crisp. Prefer disciplined exploration over noisy thrash.\n"
        )


def scenario_for_cycle(cycle: int, prior_fields: dict[str, Any] | None) -> dict[str, str]:
    best = BASELINES[min(cycle - 1, len(BASELINES) - 1)]
    if cycle == 1:
        a1_summary = (
            "Cycle 1 summary for agent A1\n\n"
            "- Explored learning-rate and warmup changes.\n"
            f"- Best run stayed near val_bpb={best:.4f} with lr=6e-4 and short warmup.\n"
            "- lr>=1e-3 diverged consistently.\n"
            "- Keeping heads fixed at 8 looked safer than changing attention width."
        )
        a2_summary = (
            "Cycle 1 summary for agent A2\n\n"
            "- Explored dropout and weight-decay combinations.\n"
            "- Best run improved slightly with dropout around 0.1 and moderate weight decay.\n"
            "- batch_size>=128 looked worse on this setup.\n"
            "- Changing too many regularization knobs at once made the signal noisy."
        )
        dead_ends = "lr >= 1e-3 diverges; batch_size >= 128 regresses; multi-knob changes add noise"
        assignment_a1 = "A1 next: optimizer-only refinement around lr 4e-4 to 8e-4 with warmup 5 to 20; keep heads 8 and dropout 0.1 fixed."
        assignment_a2 = "A2 next: regularization-only refinement over dropout and weight decay near the current basin; keep lr 6e-4 and heads 8 fixed."
    else:
        prior_best = str((prior_fields or {}).get("best_result_summary", {}).get("value") or "")
        prior_dead = str((prior_fields or {}).get("dead_ends_summary", {}).get("value") or "")
        a1_summary = (
            f"Cycle {cycle} summary for agent A1\n\n"
            f"- Entered with prior shared best: {prior_best}\n"
            "- Followed the optimizer-only assignment and narrowed lr/warmup around the previous basin.\n"
            f"- Local best now looks near val_bpb={best:.4f}.\n"
            f"- Previously carried dead ends still hold: {prior_dead}"
        )
        a2_summary = (
            f"Cycle {cycle} summary for agent A2\n\n"
            f"- Entered with prior shared best: {prior_best}\n"
            "- Followed the regularization-only assignment and reduced duplicate search.\n"
            f"- Local best now looks compatible with val_bpb={best:.4f}.\n"
            f"- Previously carried dead ends still hold: {prior_dead}"
        )
        extra_dead = [
            "warmup > 40 regresses",
            "dropout > 0.2 regresses",
            "weight_decay < 0.01 under-regularizes",
            "late architecture changes destroy comparability",
        ][min(cycle - 2, 3)]
        dead_ends = f"{prior_dead}; {extra_dead}" if prior_dead else extra_dead
        assignment_a1 = f"A1 next: continue optimizer-only refinement in a narrower lr and warmup pocket for cycle {cycle}; keep architecture fixed."
        assignment_a2 = f"A2 next: continue regularization-only refinement around dropout and weight decay for cycle {cycle}; keep optimizer basin fixed."
    best_result_summary = (
        f"Shared best basin after cycle {cycle}: val_bpb {best:.4f} around lr 6e-4, warmup 10, heads 8, dropout 0.1."
    )
    return {
        "a1_summary": a1_summary,
        "a2_summary": a2_summary,
        "best_result_summary": best_result_summary,
        "dead_ends_summary": dead_ends,
        "assignment_a1": assignment_a1,
        "assignment_a2": assignment_a2,
    }


def build_prior_summary(fields: dict[str, Any]) -> str:
    best = str(fields.get("best_result_summary", {}).get("value") or "").strip()
    dead = str(fields.get("dead_ends_summary", {}).get("value") or "").strip()
    a1 = str(fields.get("assignment_a1", {}).get("value") or "").strip()
    a2 = str(fields.get("assignment_a2", {}).get("value") or "").strip()
    return f"Best: {best} Dead ends: {dead} Next split: {a1} | {a2}".strip()


def create_room(base_url: str, *, cycle: int, parent_room_id: str | None, prior_outcome_summary: str | None) -> dict[str, Any]:
    args = SimpleNamespace(
        topic=f"autoresearch sync - dry run cycle {cycle}",
        goal="Share findings, mark dead ends, and split the next exploration direction without duplicate work.",
        participants=["agent_a1", "agent_a2"],
        required_field=list(DEFAULT_REQUIRED_FIELDS),
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


def run_cycle(base_url: str, workspace_root: Path, *, cycle: int, parent_room_id: str | None, prior_outcome_summary: str | None, prior_fields: dict[str, Any] | None) -> dict[str, Any]:
    a1_program = workspace_root / "a1" / "program.md"
    a2_program = workspace_root / "a2" / "program.md"
    ensure_program(a1_program)
    ensure_program(a2_program)

    created = create_room(base_url, cycle=cycle, parent_room_id=parent_room_id, prior_outcome_summary=prior_outcome_summary)
    room = created["room"]
    room_id = room["id"]
    host_token = created["host_token"]
    token_a1 = created["invites"]["agent_a1"]
    token_a2 = created["invites"]["agent_a2"]
    scenario = scenario_for_cycle(cycle, prior_fields)

    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/join",
        token=token_a1,
        payload={"client_name": f"a1-cycle-{cycle}", "context_envelope": {"summary": scenario["a1_summary"], "refs": []}},
    )
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/join",
        token=token_a2,
        payload={"client_name": f"a2-cycle-{cycle}", "context_envelope": {"summary": scenario["a2_summary"], "refs": []}},
    )

    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token_a1,
        payload={
            "intent": "NOTE",
            "text": f"Phase 1 sync from A1 for cycle {cycle}: explored optimizer-side changes, current best is consistent with the shared basin, and the main open question is how tightly to narrow lr and warmup next.",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": True,
            "meta": {},
        },
    )
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token_a2,
        payload={
            "intent": "ANSWER",
            "text": f"Phase 1 sync from A2 for cycle {cycle}: regularization-side changes are compatible with the shared basin, duplicate exploration should be avoided, and the split should stay optimizer-only vs regularization-only.",
            "fills": {},
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        },
    )
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token_a1,
        payload={
            "intent": "ANSWER",
            "text": f"Shared state is aligned for cycle {cycle}. Locking the next-cycle split and dead ends now.",
            "fills": {
                "best_result_summary": scenario["best_result_summary"],
                "dead_ends_summary": scenario["dead_ends_summary"],
                "assignment_a1": scenario["assignment_a1"],
                "assignment_a2": scenario["assignment_a2"],
            },
            "facts": [],
            "questions": [],
            "expect_reply": False,
            "meta": {},
        },
    )
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token_a1,
        payload={"intent": "DONE", "text": "DONE", "fills": {}, "facts": [], "questions": [], "expect_reply": False, "meta": {}},
    )
    http_json(
        "POST",
        f"{api_base_url(base_url)}/rooms/{room_id}/messages",
        token=token_a2,
        payload={"intent": "DONE", "text": "DONE", "fills": {}, "facts": [], "questions": [], "expect_reply": False, "meta": {}},
    )

    deadline = time.time() + 20
    result: dict[str, Any] | None = None
    while time.time() < deadline:
        result = request_monitor_result(base_url=base_url, room_id=room_id, host_token=host_token)
        if (result.get("result") or {}).get("status") == "closed":
            break
        time.sleep(0.25)
    if not result or (result.get("result") or {}).get("status") != "closed":
        raise RuntimeError(f"cycle {cycle} room did not close: {room_id}")

    fields = (result.get("result") or {}).get("fields") or {}
    apply_assignment_to_program(a1_program, fields, "assignment_a1")
    apply_assignment_to_program(a2_program, fields, "assignment_a2")

    state_dir = workspace_root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"cycle_{cycle}_room.json").write_text(json.dumps(created, ensure_ascii=False, indent=2))
    (state_dir / f"cycle_{cycle}_result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))

    return {
        "cycle": cycle,
        "room_id": room_id,
        "host_token": host_token,
        "parent_room_id": parent_room_id,
        "prior_outcome_summary": prior_outcome_summary,
        "result": result.get("result") or {},
        "program_paths": {"a1": str(a1_program), "a2": str(a2_program)},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a fully scripted fake multi-cycle autoresearch sync chain")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--workspace-root", default=str(ROOT / ".tmp/autoresearch_sync_demo_chain"))
    parser.add_argument("--cycles", type=int, default=3)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    workspace_root = Path(args.workspace_root)
    cycles = max(1, int(args.cycles))
    parent_room_id: str | None = None
    prior_outcome_summary: str | None = None
    prior_fields: dict[str, Any] | None = None
    outputs: list[dict[str, Any]] = []
    for cycle in range(1, cycles + 1):
        out = run_cycle(
            api_base_url(args.base_url),
            workspace_root,
            cycle=cycle,
            parent_room_id=parent_room_id,
            prior_outcome_summary=prior_outcome_summary,
            prior_fields=prior_fields,
        )
        outputs.append(out)
        parent_room_id = out["room_id"]
        prior_fields = out["result"].get("fields") or {}
        prior_outcome_summary = build_prior_summary(prior_fields)
    print(json.dumps({"workspace_root": str(workspace_root), "cycles": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
