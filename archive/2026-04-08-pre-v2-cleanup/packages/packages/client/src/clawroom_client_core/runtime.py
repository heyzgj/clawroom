from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal


RunnerStrategy = Literal["inline-safe", "daemon-safe", "manual-only"]
RunnerStatus = Literal["pending", "ready", "active", "idle", "waiting_owner", "stalled", "restarting", "replaced", "exited", "abandoned"]
RecoveryPolicy = Literal["automatic", "takeover_only"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(slots=True)
class ConversationMemory:
    owner_context: str = ""
    latest_commitments: list[str] = field(default_factory=list)
    pending_owner_req_id: str | None = None
    last_counterpart_ask: str = ""
    last_counterpart_intent: str = ""
    last_counterpart_message: str = ""

    def note_owner_context(self, value: str) -> None:
        self.owner_context = str(value or "").strip()

    def note_counterpart_message(self, *, intent: str, text: str) -> None:
        self.last_counterpart_intent = str(intent or "").upper().strip()
        self.last_counterpart_message = str(text or "").strip()[:500]
        if self.last_counterpart_intent == "ASK":
            self.last_counterpart_ask = str(text or "").strip()[:500]

    def remember_commitment(self, text: str, *, limit: int = 6) -> None:
        cleaned = str(text or "").strip()
        if not cleaned:
            return
        if cleaned in self.latest_commitments:
            self.latest_commitments.remove(cleaned)
        self.latest_commitments.append(cleaned[:500])
        if len(self.latest_commitments) > limit:
            self.latest_commitments = self.latest_commitments[-limit:]

    def to_payload(self) -> dict[str, Any]:
        return {
            "owner_context": self.owner_context,
            "latest_commitments": list(self.latest_commitments),
            "pending_owner_req_id": self.pending_owner_req_id,
            "last_counterpart_ask": self.last_counterpart_ask,
            "last_counterpart_intent": self.last_counterpart_intent,
            "last_counterpart_message": self.last_counterpart_message,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> ConversationMemory:
        if not isinstance(raw, dict):
            return cls()
        commitments = raw.get("latest_commitments")
        cleaned_commitments = (
            [str(x).strip()[:500] for x in commitments if str(x).strip()]
            if isinstance(commitments, list)
            else []
        )
        pending_owner = raw.get("pending_owner_req_id")
        pending_owner_req_id = str(pending_owner).strip()[:120] if pending_owner else None
        return cls(
            owner_context=str(raw.get("owner_context") or "").strip(),
            latest_commitments=cleaned_commitments[-6:],
            pending_owner_req_id=pending_owner_req_id,
            last_counterpart_ask=str(raw.get("last_counterpart_ask") or "").strip()[:500],
            last_counterpart_intent=str(raw.get("last_counterpart_intent") or "").upper().strip()[:40],
            last_counterpart_message=str(raw.get("last_counterpart_message") or "").strip()[:500],
        )


@dataclass(slots=True)
class RunnerCapabilities:
    strategy: RunnerStrategy = "manual-only"
    owner_reply_supported: bool = False
    background_safe: bool = False
    persistence_supported: bool = False
    health_surface: bool = True
    managed_certified: bool = False
    recovery_policy: RecoveryPolicy = "takeover_only"
    supervision_origin: str = "direct"
    replacement_count: int = 0
    supersedes_run_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "owner_reply_supported": self.owner_reply_supported,
            "background_safe": self.background_safe,
            "persistence_supported": self.persistence_supported,
            "health_surface": self.health_surface,
            "managed_certified": self.managed_certified,
            "recovery_policy": self.recovery_policy,
            "supervision_origin": self.supervision_origin,
            "replacement_count": self.replacement_count,
            "supersedes_run_id": self.supersedes_run_id,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> RunnerCapabilities:
        if not isinstance(raw, dict):
            return cls()
        strategy = str(raw.get("strategy") or "manual-only").strip()
        if strategy not in {"inline-safe", "daemon-safe", "manual-only"}:
            strategy = "manual-only"
        recovery_policy = str(raw.get("recovery_policy") or "takeover_only").strip()
        if recovery_policy not in {"automatic", "takeover_only"}:
            recovery_policy = "takeover_only"
        supervision_origin = str(raw.get("supervision_origin") or "direct").strip().lower()
        if supervision_origin not in {"runnerd", "direct", "shell", "unknown"}:
            supervision_origin = "unknown"
        replacement_count = raw.get("replacement_count")
        try:
            normalized_replacement_count = max(0, int(replacement_count or 0))
        except (TypeError, ValueError):
            normalized_replacement_count = 0
        supersedes_run_id = str(raw.get("supersedes_run_id") or "").strip()[:120] or None
        return cls(
            strategy=strategy,  # type: ignore[arg-type]
            owner_reply_supported=bool(raw.get("owner_reply_supported")),
            background_safe=bool(raw.get("background_safe")),
            persistence_supported=bool(raw.get("persistence_supported")),
            health_surface=bool(raw.get("health_surface", True)),
            managed_certified=bool(raw.get("managed_certified")),
            recovery_policy=recovery_policy,  # type: ignore[arg-type]
            supervision_origin=supervision_origin,
            replacement_count=normalized_replacement_count,
            supersedes_run_id=supersedes_run_id,
        )


@dataclass(slots=True)
class RunnerHealth:
    status: RunnerStatus = "ready"
    updated_at: str = field(default_factory=_now_iso)
    last_error: str = ""
    recent_note: str = ""
    log_path: str = ""

    def set(
        self,
        *,
        status: RunnerStatus,
        last_error: str = "",
        recent_note: str = "",
        log_path: str | None = None,
    ) -> None:
        self.status = status
        self.updated_at = _now_iso()
        self.last_error = str(last_error or "").strip()[:500]
        self.recent_note = str(recent_note or "").strip()[:500]
        if log_path is not None:
            self.log_path = str(log_path).strip()

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "updated_at": self.updated_at,
            "last_error": self.last_error,
            "recent_note": self.recent_note,
            "log_path": self.log_path,
        }

    @classmethod
    def from_raw(cls, raw: Any) -> RunnerHealth:
        if not isinstance(raw, dict):
            return cls()
        status = str(raw.get("status") or "ready").strip()
        if status not in {"pending", "ready", "active", "idle", "waiting_owner", "stalled", "restarting", "replaced", "exited", "abandoned"}:
            status = "ready"
        return cls(
            status=status,  # type: ignore[arg-type]
            updated_at=str(raw.get("updated_at") or _now_iso()),
            last_error=str(raw.get("last_error") or "").strip()[:500],
            recent_note=str(raw.get("recent_note") or "").strip()[:500],
            log_path=str(raw.get("log_path") or "").strip(),
        )
