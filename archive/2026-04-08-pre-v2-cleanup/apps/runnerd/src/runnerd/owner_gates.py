from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


@dataclass(slots=True)
class PendingInboxOwnerGate:
    owner_request_id: str
    run_id: str | None
    room_id: str
    participant: str
    agent_id: str
    runtime: str | None
    display_name: str | None
    topic: str
    goal: str
    deadline_at: str | None
    required_fields: list[str]
    text: str
    event_id: int
    created_at_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_request_id": self.owner_request_id,
            "run_id": self.run_id,
            "room_id": self.room_id,
            "participant": self.participant,
            "agent_id": self.agent_id,
            "runtime": self.runtime,
            "display_name": self.display_name,
            "topic": self.topic,
            "goal": self.goal,
            "deadline_at": self.deadline_at,
            "required_fields": list(self.required_fields),
            "text": self.text,
            "event_id": self.event_id,
            "created_at_ms": self.created_at_ms,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PendingInboxOwnerGate":
        required_fields = raw.get("required_fields") if isinstance(raw.get("required_fields"), list) else []
        return cls(
            owner_request_id=str(raw.get("owner_request_id") or "").strip(),
            run_id=str(raw.get("run_id")).strip() if raw.get("run_id") is not None else None,
            room_id=str(raw.get("room_id") or "").strip(),
            participant=str(raw.get("participant") or "").strip(),
            agent_id=str(raw.get("agent_id") or "").strip(),
            runtime=str(raw.get("runtime")).strip() if raw.get("runtime") is not None else None,
            display_name=str(raw.get("display_name")).strip() if raw.get("display_name") is not None else None,
            topic=str(raw.get("topic") or "").strip(),
            goal=str(raw.get("goal") or "").strip(),
            deadline_at=str(raw.get("deadline_at")).strip() if raw.get("deadline_at") is not None else None,
            required_fields=[str(item).strip() for item in required_fields if str(item).strip()],
            text=str(raw.get("text") or "").strip(),
            event_id=max(0, int(raw.get("event_id") or 0)),
            created_at_ms=max(0, int(raw.get("created_at_ms") or 0)),
        )


def load_pending_owner_gates(path: Path) -> dict[str, PendingInboxOwnerGate]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, PendingInboxOwnerGate] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        gate = PendingInboxOwnerGate.from_dict(value)
        if gate.owner_request_id:
            result[str(key)] = gate
    return result


def save_pending_owner_gates(path: Path, gates: Mapping[str, PendingInboxOwnerGate]) -> None:
    payload = {key: gate.to_dict() for key, gate in gates.items()}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_pending_owner_gate(
    *,
    payload: dict[str, Any],
    event_id: int,
    run_id: str | None,
    fallback_agent_id: str,
    clean_str,
) -> PendingInboxOwnerGate | None:
    owner_request_id = clean_str(payload.get("owner_request_id"))
    if not owner_request_id:
        return None
    room_id = clean_str(payload.get("room_id"))
    participant = clean_str(payload.get("participant"))
    agent_id = clean_str(payload.get("agent_id")) or fallback_agent_id
    required_fields = payload.get("required_fields") if isinstance(payload.get("required_fields"), list) else []
    return PendingInboxOwnerGate(
        owner_request_id=owner_request_id,
        run_id=run_id,
        room_id=room_id,
        participant=participant,
        agent_id=agent_id,
        runtime=clean_str(payload.get("runtime")) or None,
        display_name=clean_str(payload.get("display_name")) or None,
        topic=clean_str(payload.get("topic")),
        goal=clean_str(payload.get("goal")),
        deadline_at=clean_str(payload.get("deadline_at")) or None,
        required_fields=[str(item).strip() for item in required_fields if str(item).strip()],
        text=clean_str(payload.get("text"))[:4000],
        event_id=max(0, event_id),
        created_at_ms=max(0, int(payload.get("created_at_ms") or 0)),
    )
