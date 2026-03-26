from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_monitor_home_prompt_is_minimal() -> None:
    text = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert (
        'const INSTRUCTION_TEXT = "Read https://clawroom.cc/skill.md. Then help me with the task I send next. If another agent should join, create a ClawRoom, give me one watch link I can open plus one forwardable invite block that explains what the room is for and how to join, make the copyable part the full invite instead of only a raw link, keep watching until the room closes, and report back in plain language. If my request is short, infer a simple setup and ask at most one blocking follow-up. Do not explain ClawRoom mechanics unless I ask.";'
        in text
    ), "homepage prompt should support one-short-sentence requests without making the user write a full brief"
