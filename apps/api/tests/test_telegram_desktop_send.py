from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts" / "send_telegram_desktop_message.py"
SPEC = importlib.util.spec_from_file_location("telegram_desktop_send", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_normalize_username_strips_at() -> None:
    assert MODULE.normalize_username("@singularitygz_bot") == "singularitygz_bot"
    assert MODULE.normalize_username("link_clawd_bot") == "link_clawd_bot"


def test_new_session_applescript_uses_double_enter() -> None:
    script = MODULE.build_paste_and_send_applescript(
        extra_enter=True,
        paste_delay=0.16,
        between_enter_delay=0.35,
    )
    assert script.count("key code 36") == 2
    assert 'tell application "Telegram" to activate' in script


def test_regular_message_applescript_uses_single_enter() -> None:
    script = MODULE.build_paste_and_send_applescript(
        extra_enter=False,
        paste_delay=0.16,
        between_enter_delay=0.35,
    )
    assert script.count("key code 36") == 1


def test_telegram_send_default_new_delay_is_30_seconds() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "default=30.0" in source
