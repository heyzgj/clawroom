from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Intent(StrEnum):
    ASK = "ASK"
    ANSWER = "ANSWER"
    NOTE = "NOTE"
    DONE = "DONE"
    ASK_OWNER = "ASK_OWNER"
    OWNER_REPLY = "OWNER_REPLY"


class RoomCreateIn(BaseModel):
    topic: str = Field(..., min_length=1, max_length=500)
    goal: str = Field(..., min_length=1, max_length=2000)
    participants: list[str] = Field(..., min_length=2, max_length=8)
    required_fields: list[str] = Field(default_factory=list, max_length=64)
    turn_limit: int = Field(default=12, ge=2, le=500)
    timeout_minutes: int = Field(default=20, ge=1, le=1440)
    stall_limit: int = Field(default=3, ge=1, le=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class JoinIn(BaseModel):
    client_name: str | None = Field(default=None, max_length=120)


class LeaveIn(BaseModel):
    reason: str = Field(default="left room", max_length=500)


class CloseIn(BaseModel):
    reason: str = Field(default="manual close", max_length=500)


class MessageIn(BaseModel):
    intent: Intent = Intent.ANSWER
    text: str = Field(..., min_length=1, max_length=8000)
    fills: dict[str, str] = Field(default_factory=dict)
    facts: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    expect_reply: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_legacy_payload(cls, data: dict[str, Any]) -> "MessageIn":
        payload = dict(data)
        if "wants_reply" in payload and "expect_reply" not in payload:
            payload["expect_reply"] = bool(payload.pop("wants_reply"))
        intent = str(payload.get("intent", "ANSWER")).upper().strip()
        if intent == "NEED_HUMAN":
            payload["intent"] = Intent.ASK_OWNER
            payload.setdefault("expect_reply", False)
        return cls.model_validate(payload)
