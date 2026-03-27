from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_monitor_home_prompt_is_minimal() -> None:
    text = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert (
        'const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md. Then help me with the task I send next. After I send the task, ask me one short clarify before you create any ClawRoom. Keep that clarify to one focused question or confirmation, not a checklist. If another agent should join, create the room, give me one watch link I can open plus one forwardable full invite, keep watching until the room closes, and report back in plain language.";'
        in text
    ), "homepage prompt should support one-short-sentence requests without making the user write a full brief"
