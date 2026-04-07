from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_monitor_uses_plain_language_status_copy() -> None:
    source = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert "Working on it" in source
    assert "Needs your input" in source
    assert "Result ready" in source
    assert "Needs your attention" in source
    assert "Nothing you need to do right now." in source
    assert "The room wrapped up with all" in source
    assert "Active Sync" not in source
    assert "Owner Action Required" not in source
    assert "Captured ${filled} of ${total} expected outcomes." not in source


def test_briefing_copy_emphasizes_progress_next_step_and_outcome() -> None:
    source = (ROOT / "apps" / "monitor" / "index.html").read_text(encoding="utf-8")
    assert "Checking progress..." in source
    assert "Working on it" in source
    assert "Needs your attention" in source
    assert "Result ready" in source
    assert "More details" in source
    assert "Checking room status..." not in source
    assert "All quiet" not in source
    assert "Execution details" not in source


def test_homepage_copy_supports_short_requests() -> None:
    source = (ROOT / "apps" / "monitor" / "index.html").read_text(encoding="utf-8")
    assert 'wait for "ready", then send your task in one short sentence' in source
    assert "Most people use three short messages" in source
    assert "watch link you can open and a forwardable invite" in source
    assert "you get a watch link for yourself and a forwardable invite" in source
    assert "help me decide dinner" in source
    assert 'wait for "ready", send one short ask, then answer one short clarify before the room opens.' in source
    assert "create a room:" not in source


def test_monitor_script_supports_participant_watch_links() -> None:
    source = (ROOT / "apps" / "monitor" / "src" / "main.js").read_text(encoding="utf-8")
    assert "participant_token" in source
    assert "p.get('token')" in source
    assert "State.authMode === 'host' ? 'host_token' : 'token'" in source
    assert "/rooms/${this.roomId}/stream" in source
