from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx

from .models import WakePackage
from .node_status import InboxPollingConfig


@dataclass(slots=True)
class InboxPollResult:
    processed_any: bool
    cursor: int
    last_event_id: int


def load_inbox_cursor(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(raw, dict):
        return 0
    return max(0, int(raw.get("cursor") or 0))


def save_inbox_cursor(path: Path, cursor: int) -> None:
    path.write_text(json.dumps({"cursor": int(cursor)}, ensure_ascii=False, indent=2), encoding="utf-8")


def build_presence_payload(config: InboxPollingConfig) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "agent_id": config.agent_id,
        "name": config.display_name,
        "runtime": config.runner_kind,
        "capabilities": [],
        "inbox_token": config.inbox_token,
    }
    if config.managed_runnerd_url:
        payload["managed_runnerd_url"] = config.managed_runnerd_url
    return payload


def sync_inbox_presence(*, config: InboxPollingConfig, timeout_seconds: float = 20.0) -> None:
    payload = build_presence_payload(config)
    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        response = client.post(f"{config.base_url}/agents", json=payload)
    response.raise_for_status()


def poll_inbox_once(
    *,
    config: InboxPollingConfig,
    cursor: int,
    process_event: Callable[[dict[str, Any]], None],
) -> InboxPollResult:
    url = (
        f"{config.base_url}/agents/{config.agent_id}/inbox"
        f"?after={cursor}&wait={config.wait_seconds}"
    )
    headers = {"Authorization": f"Bearer {config.inbox_token}"}
    with httpx.Client(timeout=max(10.0, config.wait_seconds + 5.0), trust_env=False) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    body = response.json()
    events = body.get("events") if isinstance(body, dict) else []
    processed_any = False
    next_cursor = cursor
    last_event_id = 0
    if isinstance(events, list):
        for event in events:
            event_id = int(event.get("id") or 0)
            process_event(event)
            if event_id > 0:
                next_cursor = max(next_cursor, event_id)
                last_event_id = max(last_event_id, event_id)
            processed_any = True
    body_next_cursor = int(body.get("next_cursor") or next_cursor) if isinstance(body, dict) else next_cursor
    next_cursor = max(next_cursor, body_next_cursor)
    return InboxPollResult(
        processed_any=processed_any,
        cursor=next_cursor,
        last_event_id=max(last_event_id, next_cursor if processed_any else last_event_id),
    )


def build_wake_package_from_invite(
    *,
    payload: dict[str, Any],
    event_id: int,
    config: InboxPollingConfig,
    clean_str: Callable[[Any], str],
) -> WakePackage:
    room_id = clean_str(payload.get("room_id"))
    join_link = clean_str(payload.get("join_link"))
    participant = clean_str(payload.get("participant"))
    topic = clean_str(payload.get("topic"))
    goal = clean_str(payload.get("goal"))
    invited_by = clean_str(payload.get("invited_by")) or "unknown_owner"
    preferred_runnerd_url = clean_str(payload.get("managed_runnerd_url")) or clean_str(payload.get("preferred_runnerd_url"))
    owner_context_override = clean_str(payload.get("owner_context"))[:4000]
    required_fields = payload.get("required_fields") if isinstance(payload.get("required_fields"), list) else []
    expected_output = ", ".join(str(item).strip() for item in required_fields if str(item).strip())[:4000]
    task_summary = f"{topic}: {goal}".strip(": ").strip() or f"Join room {room_id}"
    owner_context = owner_context_override or f"Invited by {invited_by} as participant {participant or config.agent_id}."
    return WakePackage.model_validate({
        "coordination_id": f"inbox:{config.agent_id}:{room_id}:{participant or config.agent_id}",
        "wake_request_id": f"inboxevt_{event_id}",
        "room_id": room_id,
        "join_link": join_link,
        "role": "auto",
        "task_summary": task_summary,
        "owner_context": owner_context,
        "expected_output": expected_output or "Join the room and help complete the required fields.",
        "preferred_runner_kind": config.runner_kind,
        "preferred_runnerd_url": preferred_runnerd_url or None,
        "sender_owner_label": invited_by,
        "sender_gateway_label": config.gateway_label,
    })
