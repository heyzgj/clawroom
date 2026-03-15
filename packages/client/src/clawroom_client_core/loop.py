from __future__ import annotations

from typing import Any

from .state import RunnerState


def _event_id(evt: dict[str, Any]) -> int:
    raw = evt.get("id")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.isdigit():
        return int(raw)
    return 0


def relay_requires_reply(evt: dict[str, Any]) -> bool:
    msg = (evt.get("payload") or {}).get("message") or {}
    intent = str(msg.get("intent", "")).upper().strip()
    expect_reply = bool(msg.get("expect_reply", True))
    return intent == "DONE" or expect_reply


def next_relays(batch: dict[str, Any], state: RunnerState) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    room = batch.get("room") if isinstance(batch.get("room"), dict) else {}
    events = batch.get("events") if isinstance(batch.get("events"), list) else []
    raw_next = batch.get("next_cursor")

    relays: list[dict[str, Any]] = []
    for evt in events:
        if not isinstance(evt, dict):
            continue
        event_id = _event_id(evt)
        if event_id <= 0 or state.is_seen(event_id):
            continue
        state.mark_seen(event_id)
        if evt.get("type") == "relay":
            relays.append(evt)

    next_cursor = state.cursor
    if isinstance(raw_next, int):
        next_cursor = max(next_cursor, raw_next)
    elif isinstance(raw_next, str) and raw_next.isdigit():
        next_cursor = max(next_cursor, int(raw_next))
    state.cursor = next_cursor
    return room, relays, next_cursor

