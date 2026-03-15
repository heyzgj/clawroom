from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "skills" / "openclaw-telegram-e2e" / "scripts"))

import telegram_desktop


def test_build_message_plan_includes_hardened_new_step() -> None:
    plan = telegram_desktop.build_message_plan(
        text="Read https://clawroom.cc/skill.md first.",
        reset_session=True,
        wait_after_new=1.8,
    )
    assert len(plan) == 2
    assert plan[0].text == "/new"
    assert plan[0].double_enter is True
    assert plan[1].text == "Read https://clawroom.cc/skill.md first."
    assert plan[1].double_enter is False


def test_build_resolve_url_normalizes_bot_target() -> None:
    assert telegram_desktop.build_resolve_url("@singularitygz_bot") == "tg://resolve?domain=singularitygz_bot"
    assert telegram_desktop.build_resolve_url("https://t.me/link_clawd_bot") == "tg://resolve?domain=link_clawd_bot"


def test_build_paste_and_send_script_double_enter_adds_second_return() -> None:
    single = telegram_desktop.build_paste_and_send_script(double_enter=False)
    double = telegram_desktop.build_paste_and_send_script(double_enter=True)
    assert sum(1 for line in single if "key code 36" in line) == 1
    assert sum(1 for line in double if "key code 36" in line) == 2
