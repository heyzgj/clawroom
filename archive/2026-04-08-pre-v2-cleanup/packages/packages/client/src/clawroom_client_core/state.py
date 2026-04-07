from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .runtime import ConversationMemory, RunnerCapabilities, RunnerHealth, RunnerStatus


LogFn = Callable[[str], None]


@dataclass(slots=True)
class RunnerState:
    base_url: str
    room_id: str
    token: str
    participant: str | None = None
    cursor: int = 0
    seen_event_ids: set[int] = field(default_factory=set)
    state_path: Path | None = None
    runtime_session_id: str | None = None
    runner_id: str | None = None
    attempt_id: str | None = None
    execution_mode: str = "compatibility"
    lease_expires_at: str | None = None
    conversation: ConversationMemory = field(default_factory=ConversationMemory)
    capabilities: RunnerCapabilities = field(default_factory=RunnerCapabilities)
    health: RunnerHealth = field(default_factory=RunnerHealth)

    def is_seen(self, event_id: int) -> bool:
        return event_id in self.seen_event_ids

    def mark_seen(self, event_id: int) -> None:
        if event_id > 0:
            self.seen_event_ids.add(event_id)

    def note_owner_context(self, value: str) -> None:
        self.conversation.note_owner_context(value)

    def note_counterpart_message(self, *, intent: str, text: str) -> None:
        self.conversation.note_counterpart_message(intent=intent, text=text)

    def note_commitment(self, text: str) -> None:
        self.conversation.remember_commitment(text)

    def set_pending_owner_request(self, owner_req_id: str | None) -> None:
        self.conversation.pending_owner_req_id = str(owner_req_id or "").strip()[:120] or None

    def set_capabilities(self, capabilities: RunnerCapabilities) -> None:
        self.capabilities = capabilities

    def set_health(
        self,
        *,
        status: RunnerStatus,
        last_error: str = "",
        recent_note: str = "",
        log_path: str | None = None,
    ) -> None:
        self.health.set(status=status, last_error=last_error, recent_note=recent_note, log_path=log_path)

    def save(self, *, logger: LogFn | None = None) -> bool:
        if not self.state_path:
            return False
        payload = {
            "base_url": self.base_url,
            "room_id": self.room_id,
            "token": self.token,
            "participant": self.participant,
            "cursor": int(self.cursor),
            "seen_event_ids": sorted(int(x) for x in self.seen_event_ids if int(x) > 0),
            "runtime_session_id": self.runtime_session_id,
            "runner_id": self.runner_id,
            "attempt_id": self.attempt_id,
            "execution_mode": self.execution_mode,
            "lease_expires_at": self.lease_expires_at,
            "conversation": self.conversation.to_payload(),
            "capabilities": self.capabilities.to_payload(),
            "health": self.health.to_payload(),
        }
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.state_path)
            return True
        except Exception as exc:  # noqa: BLE001
            if logger:
                logger(f"state_save_failed path={self.state_path} error={exc}")
            return False


def _load_state(path: Path, *, logger: LogFn | None = None) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger(f"state_load_failed path={path} error={exc}")
        return None
    if not isinstance(raw, dict):
        if logger:
            logger(f"state_load_invalid path={path} reason=not_object")
        return None
    return raw


def build_runner_state(
    *,
    base_url: str,
    room_id: str,
    token: str,
    participant: str | None = None,
    initial_cursor: int = 0,
    state_path: Path | None = None,
    runtime_session_id: str | None = None,
    logger: LogFn | None = None,
) -> RunnerState:
    state = RunnerState(
        base_url=base_url.rstrip("/"),
        room_id=room_id,
        token=token,
        participant=participant,
        cursor=max(0, int(initial_cursor)),
        state_path=state_path,
        runtime_session_id=runtime_session_id,
    )
    if not state_path:
        return state

    loaded = _load_state(state_path, logger=logger)
    if not loaded:
        return state

    loaded_room = str(loaded.get("room_id") or "")
    loaded_base = str(loaded.get("base_url") or "").rstrip("/")
    if loaded_room and loaded_room != room_id:
        if logger:
            logger(f"state_room_mismatch expected={room_id} loaded={loaded_room}; ignoring file")
        return state
    if loaded_base and loaded_base != state.base_url:
        if logger:
            logger(f"state_base_mismatch expected={state.base_url} loaded={loaded_base}; ignoring file")
        return state

    loaded_cursor = loaded.get("cursor")
    if isinstance(loaded_cursor, int):
        state.cursor = max(state.cursor, loaded_cursor)

    loaded_seen = loaded.get("seen_event_ids")
    if isinstance(loaded_seen, list):
        state.seen_event_ids = {int(x) for x in loaded_seen if isinstance(x, int) and x > 0}

    loaded_participant = loaded.get("participant")
    if isinstance(loaded_participant, str) and loaded_participant.strip():
        state.participant = loaded_participant.strip()

    loaded_session = loaded.get("runtime_session_id")
    if isinstance(loaded_session, str) and loaded_session.strip():
        state.runtime_session_id = loaded_session.strip()

    loaded_runner_id = loaded.get("runner_id")
    if isinstance(loaded_runner_id, str) and loaded_runner_id.strip():
        state.runner_id = loaded_runner_id.strip()

    loaded_attempt_id = loaded.get("attempt_id")
    if isinstance(loaded_attempt_id, str) and loaded_attempt_id.strip():
        state.attempt_id = loaded_attempt_id.strip()

    loaded_execution_mode = loaded.get("execution_mode")
    if isinstance(loaded_execution_mode, str) and loaded_execution_mode.strip():
        state.execution_mode = loaded_execution_mode.strip()

    loaded_lease_expires_at = loaded.get("lease_expires_at")
    if isinstance(loaded_lease_expires_at, str) and loaded_lease_expires_at.strip():
        state.lease_expires_at = loaded_lease_expires_at.strip()

    state.conversation = ConversationMemory.from_raw(loaded.get("conversation"))
    state.capabilities = RunnerCapabilities.from_raw(loaded.get("capabilities"))
    state.health = RunnerHealth.from_raw(loaded.get("health"))

    if logger:
        logger(
            f"state_loaded path={state_path} cursor={state.cursor} seen={len(state.seen_event_ids)}"
        )
    return state
