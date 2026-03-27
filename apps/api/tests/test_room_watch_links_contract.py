from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def test_worker_room_exposes_owner_and_participant_watch_links() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert "const DEFAULT_PUBLIC_UI_BASE = \"https://clawroom.cc\";" in source
    assert "private publicUiBase(origin?: string): string {" in source
    assert "monitor_link: monitorLink" in source
    assert 'const monitorLink = `${publicUiBase}/?room_id=${encodeURIComponent(roomId)}&host_token=${encodeURIComponent(hostToken)}`;' in source
    assert 'watch_link: `${publicUiBase}/?room_id=${encodeURIComponent(roomId)}&token=${encodeURIComponent(participantToken)}`' in source


def test_worker_room_accepts_participant_query_tokens_for_events_and_result() -> None:
    source = (ROOT / "apps" / "edge" / "src" / "worker_room.ts").read_text(encoding="utf-8")
    assert 'audience = await this.requireParticipantFromQuery(request, { joined: true });' in source
    assert "await this.requireParticipantFromQuery(request);" in source
