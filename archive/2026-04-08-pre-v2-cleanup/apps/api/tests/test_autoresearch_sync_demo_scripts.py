from __future__ import annotations

from argparse import Namespace
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.autoresearch_sync_demo.common import parse_fill_pairs, parse_refs_arg, split_assignment_text
from scripts.autoresearch_sync_demo.orchestrator import (
    DEFAULT_REQUIRED_FIELDS,
    SYNC_BLOCK_END,
    SYNC_BLOCK_START,
    apply_assignment_to_program,
    build_carry_forward_summary,
    build_program_sync_block,
    build_room_payload,
    validate_sync_fields,
)


def test_parse_refs_arg_accepts_mapping_and_list() -> None:
    as_mapping = parse_refs_arg('{"best_val_bpb":"0.9412","best_commit":"abc123"}')
    assert as_mapping == [
        {"type": "custom", "label": "best_val_bpb", "value": "0.9412"},
        {"type": "custom", "label": "best_commit", "value": "abc123"},
    ]

    as_list = parse_refs_arg('[{"type":"metric","label":"best_val_bpb","value":"0.9412"}]')
    assert as_list == [{"type": "metric", "label": "best_val_bpb", "value": "0.9412"}]


def test_build_room_payload_uses_flat_required_fields_and_consensus_contract() -> None:
    args = Namespace(
        topic="autoresearch sync - cycle 2",
        goal="Share findings and split next directions.",
        participants=["agent_a1", "agent_a2"],
        required_field=[],
        turn_limit=12,
        timeout_minutes=30,
        stall_limit=3,
        min_turns=4,
        min_unique_participants=2,
        parent_room_id="room_prev123",
        prior_outcome_summary="cycle 1 dead ends were carried forward",
        prior_outcome_refs='{"best_commit":"abc123"}',
    )
    payload = build_room_payload(args)
    assert payload["required_fields"] == DEFAULT_REQUIRED_FIELDS
    assert payload["outcome_contract"]["resolution_mode"] == "consensus"
    assert payload["outcome_contract"]["close_conditions"] == {
        "min_turns": 4,
        "min_unique_participants": 2,
        "require_explicit_consensus": True,
    }
    assert payload["parent_room_id"] == "room_prev123"
    assert payload["prior_outcome_refs"] == [{"type": "custom", "label": "best_commit", "value": "abc123"}]


def test_apply_assignment_to_program_replaces_sync_block(tmp_path: Path) -> None:
    program = tmp_path / "program.md"
    program.write_text("# Existing Program\n\nKeep improving train.py.\n")
    fields = {
        "best_result_summary": {"value": "val_bpb=0.9412, lr=6e-4, heads=8, commit=abc123"},
        "dead_ends_summary": {"value": "lr>=1e-3 diverges; heads>=12 overfits"},
        "assignment_a1": {"value": "focus: fine-tune lr in [4e-4, 8e-4]; constraints: keep heads=8 fixed"},
        "assignment_a2": {"value": "focus: explore dropout; constraints: keep lr=6e-4 fixed"},
    }

    first = apply_assignment_to_program(program, fields, "assignment_a1")
    assert SYNC_BLOCK_START in first
    assert "## Current Focus" in first
    assert "fine-tune lr in [4e-4, 8e-4]" in first
    assert "- lr>=1e-3 diverges" in first

    fields["assignment_a1"] = {"value": "focus: explore warmup; constraints: keep heads=8 and lr=6e-4 fixed"}
    second = apply_assignment_to_program(program, fields, "assignment_a1")
    assert second.count(SYNC_BLOCK_START) == 1
    assert second.count(SYNC_BLOCK_END) == 1
    assert "explore warmup" in second
    assert "fine-tune lr in [4e-4, 8e-4]" not in second


def test_parse_fill_pairs_and_assignment_split_helpers() -> None:
    fills = parse_fill_pairs(["assignment_a1=focus: lr", "assignment_a2=focus: dropout"])
    assert fills == {"assignment_a1": "focus: lr", "assignment_a2": "focus: dropout"}
    focus, constraints = split_assignment_text("focus: explore warmup; constraints: keep lr fixed")
    assert focus == "explore warmup"
    assert constraints == "keep lr fixed"


def test_build_program_sync_block_is_human_readable() -> None:
    block = build_program_sync_block(
        {
            "best_result_summary": {"value": "val_bpb=0.9412 with lr=6e-4"},
            "dead_ends_summary": {"value": "lr>=1e-3 diverges; batch>=128 worse"},
            "assignment_a2": {"value": "focus: explore dropout + wd; constraints: keep lr fixed"},
        },
        "assignment_a2",
    )
    assert block.startswith(f"{SYNC_BLOCK_START}\n## Current Focus")
    assert "explore dropout + wd" in block
    assert "keep lr fixed" in block
    assert "- batch>=128 worse" in block
    assert block.endswith(f"{SYNC_BLOCK_END}\n")


def test_validate_sync_fields_requires_actionable_content() -> None:
    weak_fields = {
        "best_result_summary": {"value": "better"},
        "dead_ends_summary": {"value": "bad"},
        "assignment_a1": {"value": "focus: lr"},
        "assignment_a2": {"value": "focus: lr"},
    }
    issues = validate_sync_fields(weak_fields)
    assert any("best_result_summary" in issue for issue in issues)
    assert any("dead_ends_summary" in issue for issue in issues)
    assert any("assignment_a1" in issue for issue in issues)
    assert any("assignment_a1 and assignment_a2" in issue for issue in issues)

    strong_fields = {
        "best_result_summary": {"value": "Shared best basin is val_bpb 0.9389 around lr 5.7e-4 with warmup 9 and dropout 0.08."},
        "dead_ends_summary": {"value": "lr >= 1e-3 diverges."},
        "assignment_a1": {"value": "focus: refine lr and warmup near 5.7e-4; constraints: keep heads 8 and dropout 0.08 fixed"},
        "assignment_a2": {"value": "focus: validate nearby dropout and weight decay settings; constraints: keep lr 5.7e-4 and warmup 9 fixed"},
    }
    assert validate_sync_fields(strong_fields) == []
    assert "Previous cycle best:" in build_carry_forward_summary(strong_fields)


def test_run_coordinated_prompt_examples_match_sync_cli() -> None:
    shell = (ROOT / "scripts/autoresearch_sync_demo/run_coordinated.sh").read_text()
    assert "This is Phase 1 only." in shell
    assert "This is Phase 2 only." in shell
    assert '--intent DONE --text "DONE"' in shell
    assert 'claude -p "\\$(cat \\"$A1_PHASE1_PROMPT\\")" --allowedTools bash,edit --permission-mode dontAsk --output-format text' in shell
    assert 'codex exec "\\$(cat \\"$A2_PHASE2_PROMPT\\")"' in shell
    assert "--no-interactive" not in shell
    assert "--message" not in shell
    assert "This is a single bounded task, not an open-ended session." in shell
    assert "Do not try to close the room in this call." in shell
    assert "send one convergence message with all flat required fields filled, then send DONE, then stop." in shell
    assert "dead_ends_summary=a semicolon-separated list of the concrete dead ends you actually trust" in shell
    assert "assignment_a1=focus: ...; constraints: ..." in shell
    assert "Avoid vague wording like \"keep refining\" without saying what to hold fixed." in shell
    assert "Do not invent extra dead ends just to make the list longer." in shell
