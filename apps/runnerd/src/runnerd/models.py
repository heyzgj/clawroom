from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


WakeRole = Literal["initiator", "responder", "auto"]
RunnerKind = Literal["openclaw_bridge", "codex_bridge"]
RunStatus = Literal[
    "pending",
    "ready",
    "active",
    "idle",
    "waiting_owner",
    "stalled",
    "restarting",
    "replaced",
    "exited",
    "abandoned",
]
HopState = Literal["pending", "completed", "failed", "unknown"]


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class WakePackage(BaseModel):
    version: str = Field(default="clawroom.wake.v1")
    coordination_id: str
    wake_request_id: str
    room_id: str
    join_link: str
    role: WakeRole = "auto"
    task_summary: str
    owner_context: str = ""
    expected_output: str = ""
    deadline_at: str | None = None
    preferred_runner_kind: RunnerKind = "openclaw_bridge"
    sender_owner_label: str
    sender_gateway_label: str

    @field_validator(
        "coordination_id",
        "wake_request_id",
        "room_id",
        "sender_owner_label",
        "sender_gateway_label",
        mode="before",
    )
    @classmethod
    def _require_clean_text(cls, value: Any) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("must not be empty")
        return cleaned[:200]

    @field_validator("join_link", mode="before")
    @classmethod
    def _validate_join_link(cls, value: Any) -> str:
        cleaned = str(value or "").strip()
        if not cleaned or "token=" not in cleaned or "/join/" not in cleaned:
            raise ValueError("join_link must look like a ClawRoom join URL")
        return cleaned[:2000]

    @field_validator("task_summary", "owner_context", "expected_output", mode="before")
    @classmethod
    def _normalize_long_text(cls, value: Any) -> str:
        return str(value or "").strip()[:4000]


class WakeRequestBody(BaseModel):
    package: WakePackage

    @model_validator(mode="before")
    @classmethod
    def _coerce_top_level_package(cls, value: Any) -> Any:
        if isinstance(value, dict) and "package" in value:
            return value
        if isinstance(value, dict):
            return {"package": value}
        return value


class WakeResponse(BaseModel):
    accepted: bool
    run_id: str | None = None
    runner_kind: RunnerKind | None = None
    status: RunStatus | None = None
    reason: str | None = None


class OwnerReplyIn(BaseModel):
    text: str
    owner_request_id: str | None = None

    @field_validator("text", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError("text must not be empty")
        return cleaned[:4000]

    @field_validator("owner_request_id", mode="before")
    @classmethod
    def _normalize_req_id(cls, value: Any) -> str | None:
        cleaned = str(value or "").strip()
        return cleaned[:200] or None


class HopStatusPayload(BaseModel):
    hop: int
    label: str
    state: HopState
    code: str | None = None
    detail: str = ""
    updated_at: str = Field(default_factory=now_iso)


class PendingOwnerRequestPayload(BaseModel):
    owner_request_id: str
    text: str = ""
    updated_at: str = Field(default_factory=now_iso)


class RunPayload(BaseModel):
    run_id: str
    coordination_id: str
    wake_request_id: str
    room_id: str
    runner_kind: RunnerKind
    bridge_agent_id: str | None = None
    role: WakeRole
    status: RunStatus
    reason: str | None = None
    supersedes_run_id: str | None = None
    superseded_by_run_id: str | None = None
    pid: int | None = None
    participant: str | None = None
    runner_id: str | None = None
    attempt_id: str | None = None
    last_error: str = ""
    created_at: str
    updated_at: str
    bridge_state_path: str
    owner_reply_file: str
    log_path: str
    restart_count: int = 0
    replacement_count: int = 0
    pending_owner_request: PendingOwnerRequestPayload | None = None
    root_cause_code: str | None = None
    current_hop: int
    current_hop_label: str
    summary: str
    next_action: str | None = None
    hops: list[HopStatusPayload] = Field(default_factory=list)


WAKE_PACKAGE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", flags=re.DOTALL)


def render_wake_package(package: WakePackage) -> str:
    summary_lines = [
        "ClawRoom wake package.",
        f"Task: {package.task_summary}",
        f"Role: {package.role}",
        f"Expected output: {package.expected_output or 'Follow the room goal and return a concise result.'}",
        "If your gateway supports runnerd, pass the JSON block below to POST /wake.",
    ]
    payload = json.dumps(package.model_dump(mode="json"), ensure_ascii=False, indent=2)
    return "\n".join(summary_lines) + "\n\n```json\n" + payload + "\n```"


def parse_wake_package_text(text: str) -> WakePackage:
    match = WAKE_PACKAGE_RE.search(text or "")
    if not match:
        raise ValueError("No fenced JSON wake package block found")
    payload = json.loads(match.group(1))
    return WakePackage.model_validate(payload)
