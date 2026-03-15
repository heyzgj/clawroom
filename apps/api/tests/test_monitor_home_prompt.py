from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_monitor_home_prompt_is_minimal() -> None:
    text = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert (
        'const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md first. Create a clawroom for me.";'
        in text
    ), "homepage create-room prompt should stay minimal and rely on the skill for the rest"

