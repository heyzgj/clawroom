from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "skills" / "openclaw-telegram-e2e" / "scripts" / "telegram_desktop.py"
SPEC = importlib.util.spec_from_file_location("telegram_desktop", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_focus_message_pane_bounds_prefers_message_column() -> None:
    bounds = (100, 50, 1200, 900)
    focused = MODULE.focus_message_pane_bounds(bounds)
    assert focused is not None
    x, y, width, height = focused
    assert x > bounds[0]
    assert y > bounds[1]
    assert width < bounds[2]
    assert height < bounds[3]
    assert width >= 320
    assert height >= 320


def test_focus_message_pane_bounds_falls_back_for_small_windows() -> None:
    bounds = (0, 0, 420, 420)
    assert MODULE.focus_message_pane_bounds(bounds) == bounds


def test_preview_text_prefers_latest_visible_content() -> None:
    text = "old context " * 40 + "Gateway Status: Wake accepted and the helper is joining now."
    preview = MODULE._preview_text(text, limit=90)
    assert preview.startswith("...")
    assert "Wake accepted" in preview
    assert preview.endswith("helper is joining now.")
