from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL_PATH = ROOT / "skills" / "clawroom" / "SKILL.md"


def test_skill_is_english_only_and_keeps_one_writer_rules() -> None:
    text = SKILL_PATH.read_text(encoding="utf-8")
    cjk_pattern = re.compile(r"[\u4e00-\u9fff]")
    assert not cjk_pattern.search(text), "ClawRoom skill must stay English-only."

    required = [
        "This skill is English-only. Owner-facing chat must follow the owner's language at runtime.",
        "Do not invent owner facts. Use only confirmed facts from `owner_context.json`.",
        "After the room poller starts, this session must never write another room message.",
        "The room poller is the only room writer for that participant.",
        "python3 scripts/clawroom_preflight.py --json",
        'STATE_ROOT="$(python3 scripts/clawroom_preflight.py --print-state-root)"',
        "`status=ready`",
        "`status=not_ready`",
        "python3 scripts/clawroom_owner_reply.py --reply",
        'python3 scripts/host_start_room.py',
        '--required-field "{required_field_1}"',
        "counterpart_join_url",
        "python3 scripts/clawroom_launch_participant.py",
        '--join-url "{absolute_join_url_from_invite}"',
        "Room ready. Watch here: {absolute_monitor_link}",
        "ClawRoom Invite",
        "Do not append JSON, tokens, field names, poller instructions, or protocol notes to the owner-facing invite.",
        "The owner's forwarded invite is already permission to join.",
        'Do not ask:',
        '"Should I join?"',
        '"Go or confirm?"',
        "Before opening or joining any room, check if a room is already waiting on the owner:",
        "A room is not \"ready\" until your participant is really joined and the poller is alive.",
    ]
    for item in required:
        assert item in text

    assert "ready_candidate" not in text
    assert "shell fallback" not in text.lower()


def test_monitor_home_prompt_stays_minimal() -> None:
    text = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert "Use your installed ClawRoom skill for the next real task I send after this setup." in text
    assert "When I send it, ask at most one short clarify, then handle the room and report back in plain language." in text
    assert "raw JSON" not in text
    assert "Room ready. Watch here:" not in text


def test_export_bundle_includes_new_mini_bridge_scripts() -> None:
    export_script = (ROOT / "scripts" / "export_clawroom_skill_bundle.sh").read_text(encoding="utf-8")
    skill_text = SKILL_PATH.read_text(encoding="utf-8")
    openai_yaml = (ROOT / "skills" / "clawroom" / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert 'version: "1.3.0"' in skill_text
    assert 'short-description: Run an OpenClaw room with a mini-bridge and return an owner-ready result' in skill_text
    assert 'cp "$SOURCE_DIR/scripts/clawroom_preflight.py" "$TMP_DIR/scripts/clawroom_preflight.py"' in export_script
    assert 'cp "$SOURCE_DIR/scripts/clawroom_owner_reply.py" "$TMP_DIR/scripts/clawroom_owner_reply.py"' in export_script
    assert 'cp "$SOURCE_DIR/scripts/clawroom_launch_participant.py" "$TMP_DIR/scripts/clawroom_launch_participant.py"' in export_script
    assert 'cp "$SOURCE_DIR/scripts/host_start_room.py" "$TMP_DIR/scripts/host_start_room.py"' in export_script
    assert 'cp "$SOURCE_DIR/scripts/room_poller.py" "$TMP_DIR/scripts/room_poller.py"' in export_script
    assert 'cp "$SOURCE_DIR/scripts/state_paths.py" "$TMP_DIR/scripts/state_paths.py"' in export_script
    assert 'version 1.3.0' in export_script
    assert 'display_name: "ClawRoom"' in openai_yaml
    assert 'short_description: "Run an OpenClaw room and keep it moving until both owners get the result"' in openai_yaml
    assert 'default_prompt: "Use $clawroom to open a room with another OpenClaw, hand it to the room poller, and bring back an owner-ready result."' in openai_yaml


def test_public_references_match_skill_references() -> None:
    pairs = [
        (
            ROOT / "skills" / "clawroom" / "references" / "api.md",
            ROOT / "apps" / "monitor" / "public" / "references" / "api.md",
        ),
        (
            ROOT / "skills" / "clawroom" / "references" / "managed-gateway.md",
            ROOT / "apps" / "monitor" / "public" / "references" / "managed-gateway.md",
        ),
    ]
    for source, public in pairs:
        assert public.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")


def test_monitor_serves_source_skill_without_repo_duplicate() -> None:
    public_skill = ROOT / "apps" / "monitor" / "public" / "skill.md"
    vite_config = (ROOT / "apps" / "monitor" / "vite.config.js").read_text(encoding="utf-8")
    assert not public_skill.exists()
    assert "SKILL_SOURCE" in vite_config
    assert "'skills', 'clawroom', 'SKILL.md'" in vite_config
    assert "/skill.md" in vite_config
