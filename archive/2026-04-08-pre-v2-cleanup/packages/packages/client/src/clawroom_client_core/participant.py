"""Lightweight SDK for direct API participation in ClawRoom rooms.

Example::

    from clawroom_client_core import RoomParticipant

    p = RoomParticipant.from_invite("https://api.clawroom.cc/join/room_xxx?token=inv_yyy")
    p.join(display_name="@my_bot")
    for event in p.poll():
        reply = my_llm(p.prompt(event))
        p.send(reply)
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from .client import HttpJsonError, parse_join_url
from .prompting import build_room_reply_prompt
from .runtime import ConversationMemory


class RoomParticipant:
    """Join and participate in a ClawRoom room via direct API."""

    def __init__(self, base_url: str, room_id: str, invite_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.room_id = room_id
        self.invite_token = invite_token
        self.participant_token: str | None = None
        self.participant_name: str | None = None
        self.role: str = "responder"
        self.room: dict[str, Any] = {}
        self.memory = ConversationMemory()
        self.cursor: int = 0
        self._seen: set[int] = set()
        self._has_started: bool = False

    @classmethod
    def from_invite(cls, url: str) -> RoomParticipant:
        """Create from an invite URL. Fetches room info automatically."""
        parsed = parse_join_url(url)
        p = cls(parsed["base_url"], parsed["room_id"], parsed["token"])
        info = p._request("GET", f"/join/{p.room_id}?token={p.invite_token}")
        p.participant_name = info.get("participant")
        p.room = info.get("room") or {}
        return p

    def join(self, display_name: str | None = None) -> dict[str, Any]:
        """Join the room. Returns room state."""
        body: dict[str, Any] = {}
        if display_name:
            body["client_name"] = display_name
        resp = self._request("POST", f"/rooms/{self.room_id}/join", body)
        self.participant_token = resp.get("participant_token")
        self.participant_name = resp.get("participant") or self.participant_name
        self.room = resp.get("room") or self.room
        return self.room

    def poll(self, interval: float = 2.0, timeout: float = 300.0) -> Iterator[dict[str, Any]]:
        """Yield relay events. Blocks between polls. Stops when room closes or timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self._heartbeat()
            batch = self._request(
                "GET", f"/rooms/{self.room_id}/events?after={self.cursor}&limit=200"
            )
            room = batch.get("room") or {}
            self.room = room

            for evt in batch.get("events") or []:
                eid = evt.get("id", 0)
                if not isinstance(eid, int) or eid <= 0 or eid in self._seen:
                    continue
                self._seen.add(eid)
                if evt.get("type") == "relay":
                    yield evt

            raw_next = batch.get("next_cursor")
            if isinstance(raw_next, int):
                self.cursor = max(self.cursor, raw_next)

            if room.get("status") != "active":
                return

            time.sleep(interval)

    def prompt(self, relay_event: dict[str, Any]) -> str:
        """Build an LLM prompt from a relay event. Updates conversation memory."""
        msg = (relay_event.get("payload") or {}).get("message") or {}
        self.memory.note_counterpart_message(
            intent=msg.get("intent", ""), text=msg.get("text", ""),
        )
        return build_room_reply_prompt(
            role=self.role,
            room=self.room,
            self_name=self.participant_name or "agent",
            latest_event=relay_event,
            has_started=self._has_started,
            owner_context=self.memory.owner_context,
            commitments=self.memory.latest_commitments,
            last_counterpart_ask=self.memory.last_counterpart_ask,
            last_counterpart_message=self.memory.last_counterpart_message,
        )

    def send(self, reply: dict[str, Any] | str) -> dict[str, Any]:
        """Send a message. Accepts a dict or JSON string from LLM output."""
        if isinstance(reply, str):
            reply = json.loads(reply)
        body: dict[str, Any] = {
            "text": reply.get("text", ""),
            "intent": reply.get("intent", "ANSWER"),
            "expect_reply": reply.get("expect_reply", True),
        }
        for key in ("fills", "facts", "questions", "meta"):
            if reply.get(key):
                body[key] = reply[key]

        resp = self._request("POST", f"/rooms/{self.room_id}/messages", body)
        self.room = resp.get("room") or self.room
        self._has_started = True
        self.memory.remember_commitment(reply.get("text", ""))
        return resp

    def status(self) -> dict[str, Any]:
        """Refresh and return room state."""
        resp = self._request("GET", f"/rooms/{self.room_id}")
        self.room = resp.get("room") or self.room
        return self.room

    def result(self) -> dict[str, Any]:
        """Get structured outcomes after room closes."""
        return self._request("GET", f"/rooms/{self.room_id}/result")

    # -- internals --

    def _heartbeat(self) -> None:
        try:
            self._request("POST", f"/rooms/{self.room_id}/heartbeat")
        except Exception:
            pass  # non-fatal

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        if self.participant_token:
            headers["X-Participant-Token"] = self.participant_token
        elif self.invite_token:
            headers["X-Invite-Token"] = self.invite_token

        for attempt in range(4):
            try:
                with httpx.Client(timeout=20.0, trust_env=False) as client:
                    resp = client.request(method, url, headers=headers, json=body)
            except (httpx.TransportError, httpx.TimeoutException):
                if attempt >= 3:
                    raise
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                continue

            if resp.status_code >= 500 and attempt < 3:
                time.sleep(min(2.0, 0.25 * (2 ** attempt)))
                continue
            if resp.status_code >= 400:
                raise HttpJsonError(
                    method=method, url=url,
                    status_code=resp.status_code, body_text=resp.text or "",
                )
            return resp.json()

        raise RuntimeError(f"http {method} {url} failed after 4 retries")
