from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, create_engine, func, insert, select, update
from sqlalchemy.engine import Connection, Engine

from roombridge_core.models import MessageIn, RoomCreateIn

from .schema import (
    events,
    messages,
    metadata,
    owner_requests,
    room_fields,
    room_participants,
    room_required_fields,
    room_seen_texts,
    rooms,
)


@dataclass(slots=True)
class RoomCreateData:
    topic: str
    goal: str
    participants: list[str]
    required_fields: list[str]
    turn_limit: int
    timeout_minutes: int
    stall_limit: int
    metadata: dict[str, Any]


def now_utc() -> datetime:
    return datetime.now(UTC)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def norm_name(value: str) -> str:
    return " ".join(value.strip().lower().split())


def norm_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def clean_participants(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        n = norm_name(item)
        if not n or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def clean_fields(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        key = item.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_token() -> str:
    return secrets.token_urlsafe(24)


class RoomStore:
    def __init__(self, dsn: str):
        self._engine: Engine = create_engine(dsn, future=True, pool_pre_ping=True)

    @property
    def engine(self) -> Engine:
        return self._engine

    def init(self) -> None:
        metadata.create_all(self._engine)

    def reset_all(self) -> None:
        metadata.drop_all(self._engine)
        metadata.create_all(self._engine)

    def create_room(self, payload: RoomCreateIn) -> dict[str, Any]:
        participants = clean_participants(payload.participants)
        if len(participants) < 2:
            raise ValueError("participants must include at least two unique names")
        required_fields = clean_fields(payload.required_fields)

        room_id = f"room_{uuid.uuid4().hex[:10]}"
        host_token = make_token()
        host_token_digest = token_hash(host_token)

        invites_plain: dict[str, str] = {}
        deadline = now_utc() + timedelta(minutes=payload.timeout_minutes)

        with self._engine.begin() as conn:
            conn.execute(
                insert(rooms).values(
                    id=room_id,
                    topic=payload.topic.strip(),
                    goal=payload.goal.strip(),
                    status="active",
                    stop_reason=None,
                    stop_detail=None,
                    turn_limit=payload.turn_limit,
                    turn_count=0,
                    stall_limit=payload.stall_limit,
                    stall_count=0,
                    timeout_minutes=payload.timeout_minutes,
                    deadline_at=deadline,
                    created_at=now_utc(),
                    closed_at=None,
                    metadata_json=payload.metadata,
                    host_token_hash=host_token_digest,
                )
            )

            for idx, name in enumerate(participants):
                token = make_token()
                invites_plain[name] = token
                conn.execute(
                    insert(room_participants).values(
                        room_id=room_id,
                        name=name,
                        position=idx,
                        invite_token_hash=token_hash(token),
                        client_name=None,
                        joined=False,
                        online=False,
                        done=False,
                        waiting_owner=False,
                        joined_at=None,
                        last_seen_at=None,
                    )
                )

            for field_key in required_fields:
                conn.execute(insert(room_required_fields).values(room_id=room_id, field_key=field_key))

            self._emit_event(conn, room_id, "*", "status", {"status": "active", "reason": "room_created"})

            snapshot = self._room_snapshot(conn, room_id)

        return {
            "room": snapshot,
            "host_token": host_token,
            "host_link": f"/rooms/{room_id}/monitor?host_token={host_token}",
            "invites": invites_plain,
        }

    def invite_info(self, invite_token: str) -> dict[str, Any]:
        digest = token_hash(invite_token)
        with self._engine.begin() as conn:
            row = conn.execute(
                select(room_participants.c.room_id, room_participants.c.name).where(
                    room_participants.c.invite_token_hash == digest
                )
            ).mappings().first()
            if row is None:
                raise LookupError("invite not found")
            room = self._room_snapshot(conn, row["room_id"])
            return {
                "room": room,
                "participant": row["name"],
            }

    def join(self, room_id: str, token: str, client_name: str | None) -> dict[str, Any]:
        with self._engine.begin() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._require_room_active(conn, room_id)
            ts = now_utc()
            conn.execute(
                update(room_participants)
                .where(room_participants.c.id == participant["id"])
                .values(
                    joined=True,
                    online=True,
                    waiting_owner=False,
                    client_name=client_name,
                    joined_at=participant["joined_at"] or ts,
                    last_seen_at=ts,
                )
            )
            self._emit_event(
                conn,
                room_id,
                "*",
                "join",
                {
                    "participant": participant["name"],
                    "client_name": client_name,
                },
            )
            room = self._room_snapshot(conn, room_id)
            return {
                "participant": participant["name"],
                "room": room,
            }

    def leave(self, room_id: str, token: str, reason: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            participant = self._require_participant(conn, room_id, token)
            was_online = bool(participant["online"])
            conn.execute(
                update(room_participants)
                .where(room_participants.c.id == participant["id"])
                .values(online=False, waiting_owner=False, last_seen_at=now_utc())
            )
            self._emit_event(
                conn,
                room_id,
                "*",
                "leave",
                {
                    "participant": participant["name"],
                    "reason": reason,
                },
            )
            room = self._room_snapshot(conn, room_id)
            return {"room": room, "was_online": was_online}

    def close(self, room_id: str, host_token: str, reason: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_host(conn, room_id, host_token)
            self._close_room(conn, room_id, "manual_close", reason)
            return {"room": self._room_snapshot(conn, room_id)}

    def room_for_participant(self, room_id: str, token: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            return {"room": self._room_snapshot(conn, room_id)}

    def room_for_host(self, room_id: str, host_token: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            return {"room": self._room_snapshot(conn, room_id)}

    def post_message(self, room_id: str, token: str, message: MessageIn) -> dict[str, Any]:
        with self._engine.begin() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._require_room_active(conn, room_id)

            sender = str(participant["name"])
            now = now_utc()

            clean_fills = {
                str(k).strip(): str(v).strip()
                for k, v in (message.fills or {}).items()
                if str(k).strip() and str(v).strip()
            }
            clean_facts = [str(x).strip() for x in (message.facts or []) if str(x).strip()]
            clean_questions = [str(x).strip() for x in (message.questions or []) if str(x).strip()]

            msg_row = {
                "room_id": room_id,
                "sender": sender,
                "intent": message.intent.value,
                "text": message.text.strip(),
                "fills_json": clean_fills,
                "facts_json": clean_facts,
                "questions_json": clean_questions,
                "expect_reply": bool(message.expect_reply),
                "meta_json": message.meta or {},
                "created_at": now,
            }
            result = conn.execute(insert(messages).values(**msg_row))
            message_id = int(result.inserted_primary_key[0])

            conn.execute(
                update(room_participants)
                .where(room_participants.c.id == participant["id"])
                .values(online=True, last_seen_at=now)
            )

            new_field_count = 0
            for field_key, value in clean_fills.items():
                existing = conn.execute(
                    select(room_fields.c.id, room_fields.c.value).where(
                        and_(room_fields.c.room_id == room_id, room_fields.c.field_key == field_key)
                    )
                ).mappings().first()
                if existing is None:
                    conn.execute(
                        insert(room_fields).values(
                            room_id=room_id,
                            field_key=field_key,
                            value=value,
                            updated_by=sender,
                            updated_at=now,
                        )
                    )
                    new_field_count += 1
                elif str(existing["value"]) != value:
                    conn.execute(
                        update(room_fields)
                        .where(room_fields.c.id == existing["id"])
                        .values(value=value, updated_by=sender, updated_at=now)
                    )
                    new_field_count += 1

            text_key = norm_text(message.text)
            is_new_text = False
            if text_key:
                seen = conn.execute(
                    select(room_seen_texts.c.id).where(
                        and_(room_seen_texts.c.room_id == room_id, room_seen_texts.c.text_key == text_key)
                    )
                ).first()
                if seen is None:
                    conn.execute(insert(room_seen_texts).values(room_id=room_id, text_key=text_key))
                    is_new_text = True

            structured_progress = bool(new_field_count or clean_facts)
            has_progress = structured_progress or is_new_text

            conn.execute(update(rooms).where(rooms.c.id == room_id).values(turn_count=rooms.c.turn_count + 1))
            if has_progress:
                conn.execute(update(rooms).where(rooms.c.id == room_id).values(stall_count=0))
            elif message.intent.value not in {"DONE", "ASK_OWNER"}:
                conn.execute(
                    update(rooms).where(rooms.c.id == room_id).values(stall_count=rooms.c.stall_count + 1)
                )

            if message.intent.value == "DONE":
                conn.execute(
                    update(room_participants)
                    .where(room_participants.c.id == participant["id"])
                    .values(done=True, waiting_owner=False)
                )
            elif message.intent.value == "ASK_OWNER":
                conn.execute(
                    update(room_participants)
                    .where(room_participants.c.id == participant["id"])
                    .values(waiting_owner=True)
                )
                owner_req_id = self._upsert_owner_request(conn, room_id, sender, message_id, message.text)
                self._emit_event(
                    conn,
                    room_id,
                    "*",
                    "owner_wait",
                    {"participant": sender, "owner_req_id": owner_req_id, "text": message.text},
                )
            elif message.intent.value == "OWNER_REPLY":
                conn.execute(
                    update(room_participants)
                    .where(room_participants.c.id == participant["id"])
                    .values(waiting_owner=False)
                )
                self._resolve_owner_request(conn, room_id, sender, message.text)
                self._emit_event(
                    conn,
                    room_id,
                    "*",
                    "owner_resume",
                    {"participant": sender, "text": message.text},
                )

            message_payload = self._message_row(conn, message_id)
            self._emit_event(
                conn,
                room_id,
                "*",
                "msg",
                {
                    "message": message_payload,
                },
            )

            close_trigger = self._evaluate_rules(conn, room_id)

            relay_recipients: list[str] = []
            room_after_rules = self._require_room(conn, room_id)
            if room_after_rules["status"] == "active" and message.expect_reply:
                peers = conn.execute(
                    select(room_participants.c.name).where(
                        and_(room_participants.c.room_id == room_id, room_participants.c.name != sender)
                    )
                ).mappings().all()
                for peer in peers:
                    peer_name = str(peer["name"])
                    relay_recipients.append(peer_name)
                    self._emit_event(
                        conn,
                        room_id,
                        peer_name,
                        "relay",
                        {"from": sender, "message": message_payload},
                    )

            room_snapshot = self._room_snapshot(conn, room_id)
            return {
                "message_id": message_id,
                "sender": sender,
                "progress": {
                    "structured": structured_progress,
                    "new_text": is_new_text,
                    "new_fields": new_field_count,
                    "has_progress": has_progress,
                },
                "relay_recipients": relay_recipients,
                "host_decision": {
                    "continue": room_snapshot["status"] == "active" and bool(relay_recipients),
                    "stop_reason": room_snapshot["stop_reason"],
                    "stop_detail": room_snapshot["stop_detail"],
                    "trigger": close_trigger,
                },
                "room": room_snapshot,
            }

    def participant_events(self, room_id: str, token: str, after: int, limit: int) -> dict[str, Any]:
        with self._engine.begin() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            room = self._room_snapshot(conn, room_id)
            rows = conn.execute(
                select(events)
                .where(
                    and_(
                        events.c.room_id == room_id,
                        events.c.id > int(after),
                        events.c.audience.in_(["*", str(participant["name"])]),
                    )
                )
                .order_by(events.c.id.asc())
                .limit(max(1, min(int(limit), 500)))
            ).mappings().all()
            event_list = [self._event_row(row) for row in rows]
            next_cursor = event_list[-1]["id"] if event_list else int(after)
            return {"room": room, "events": event_list, "next_cursor": next_cursor}

    def monitor_events(self, room_id: str, host_token: str, after: int, limit: int) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            room = self._room_snapshot(conn, room_id)
            rows = conn.execute(
                select(events)
                .where(and_(events.c.room_id == room_id, events.c.id > int(after)))
                .order_by(events.c.id.asc())
                .limit(max(1, min(int(limit), 1000)))
            ).mappings().all()
            event_list = [self._event_row(row) for row in rows]
            next_cursor = event_list[-1]["id"] if event_list else int(after)
            return {"room": room, "events": event_list, "next_cursor": next_cursor}

    def participant_result(self, room_id: str, token: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            return {"result": self._result(conn, room_id)}

    def monitor_result(self, room_id: str, host_token: str) -> dict[str, Any]:
        with self._engine.begin() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            return {"result": self._result(conn, room_id)}

    def _require_room(self, conn: Connection, room_id: str) -> dict[str, Any]:
        row = conn.execute(select(rooms).where(rooms.c.id == room_id)).mappings().first()
        if row is None:
            raise LookupError("room not found")
        return dict(row)

    def _require_room_active(self, conn: Connection, room_id: str) -> dict[str, Any]:
        row = self._require_room(conn, room_id)
        if row["status"] != "active":
            raise RuntimeError(f"room is not active: {row['status']}")
        return row

    def _require_participant(self, conn: Connection, room_id: str, token: str) -> dict[str, Any]:
        digest = token_hash(token)
        row = conn.execute(
            select(room_participants).where(
                and_(
                    room_participants.c.room_id == room_id,
                    room_participants.c.invite_token_hash == digest,
                )
            )
        ).mappings().first()
        if row is None:
            raise PermissionError("invalid invite token")
        return dict(row)

    def _require_host(self, conn: Connection, room_id: str, host_token: str) -> dict[str, Any]:
        digest = token_hash(host_token)
        row = conn.execute(
            select(rooms).where(and_(rooms.c.id == room_id, rooms.c.host_token_hash == digest))
        ).mappings().first()
        if row is None:
            raise PermissionError("invalid host token")
        return dict(row)

    def _emit_event(
        self,
        conn: Connection,
        room_id: str,
        audience: str,
        type_: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            insert(events).values(
                room_id=room_id,
                audience=audience,
                type=type_,
                payload_json=payload,
                created_at=now_utc(),
            )
        )

    def _message_row(self, conn: Connection, message_id: int) -> dict[str, Any]:
        row = conn.execute(select(messages).where(messages.c.id == message_id)).mappings().first()
        if row is None:
            raise LookupError("message not found")
        return {
            "id": int(row["id"]),
            "room_id": row["room_id"],
            "sender": row["sender"],
            "intent": row["intent"],
            "text": row["text"],
            "fills": row["fills_json"] or {},
            "facts": row["facts_json"] or [],
            "questions": row["questions_json"] or [],
            "expect_reply": bool(row["expect_reply"]),
            "meta": row["meta_json"] or {},
            "created_at": self._iso(row["created_at"]),
        }

    def _event_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "room_id": row["room_id"],
            "audience": row["audience"],
            "type": row["type"],
            "payload": row["payload_json"] or {},
            "created_at": self._iso(row["created_at"]),
        }

    def _room_snapshot(self, conn: Connection, room_id: str) -> dict[str, Any]:
        room = self._require_room(conn, room_id)
        participants = conn.execute(
            select(room_participants)
            .where(room_participants.c.room_id == room_id)
            .order_by(room_participants.c.position.asc())
        ).mappings().all()
        required = conn.execute(
            select(room_required_fields.c.field_key)
            .where(room_required_fields.c.room_id == room_id)
            .order_by(room_required_fields.c.field_key.asc())
        ).mappings().all()
        fields = conn.execute(
            select(room_fields)
            .where(room_fields.c.room_id == room_id)
            .order_by(room_fields.c.field_key.asc())
        ).mappings().all()
        field_map = {
            str(item["field_key"]): {
                "value": str(item["value"]),
                "updated_by": item["updated_by"],
                "updated_at": self._iso(item["updated_at"]),
            }
            for item in fields
        }
        return {
            "id": room["id"],
            "topic": room["topic"],
            "goal": room["goal"],
            "status": room["status"],
            "stop_reason": room["stop_reason"],
            "stop_detail": room["stop_detail"],
            "turn_limit": int(room["turn_limit"]),
            "turn_count": int(room["turn_count"]),
            "stall_limit": int(room["stall_limit"]),
            "stall_count": int(room["stall_count"]),
            "timeout_minutes": int(room["timeout_minutes"]),
            "created_at": self._iso(room["created_at"]),
            "deadline_at": self._iso(room["deadline_at"]),
            "closed_at": self._iso(room["closed_at"]),
            "metadata": room["metadata_json"] or {},
            "participants": [
                {
                    "name": item["name"],
                    "client_name": item["client_name"],
                    "joined": bool(item["joined"]),
                    "online": bool(item["online"]),
                    "done": bool(item["done"]),
                    "waiting_owner": bool(item["waiting_owner"]),
                    "joined_at": self._iso(item["joined_at"]),
                    "last_seen_at": self._iso(item["last_seen_at"]),
                }
                for item in participants
            ],
            "required_fields": [str(item["field_key"]) for item in required],
            "fields": field_map,
        }

    def _evaluate_rules(self, conn: Connection, room_id: str) -> str | None:
        room = self._require_room(conn, room_id)

        required_total = int(
            conn.execute(
                select(func.count()).select_from(room_required_fields).where(
                    room_required_fields.c.room_id == room_id
                )
            ).scalar_one()
        )
        if required_total > 0:
            filled = int(
                conn.execute(
                    select(func.count())
                    .select_from(room_required_fields)
                    .join(
                        room_fields,
                        and_(
                            room_fields.c.room_id == room_required_fields.c.room_id,
                            room_fields.c.field_key == room_required_fields.c.field_key,
                        ),
                    )
                    .where(room_required_fields.c.room_id == room_id)
                ).scalar_one()
            )
            if filled >= required_total:
                self._close_room(conn, room_id, "goal_done", "all required fields filled")
                return "goal_done"

        participant_total = int(
            conn.execute(
                select(func.count()).select_from(room_participants).where(room_participants.c.room_id == room_id)
            ).scalar_one()
        )
        done_total = int(
            conn.execute(
                select(func.count()).select_from(room_participants).where(
                    and_(room_participants.c.room_id == room_id, room_participants.c.done.is_(True))
                )
            ).scalar_one()
        )
        if participant_total > 0 and done_total >= participant_total:
            self._close_room(conn, room_id, "mutual_done", "all participants sent DONE")
            return "mutual_done"

        if now_utc() >= ensure_utc(room["deadline_at"]):
            self._close_room(conn, room_id, "timeout", "room timeout reached")
            return "timeout"

        room = self._require_room(conn, room_id)
        if int(room["turn_count"]) >= int(room["turn_limit"]):
            self._close_room(conn, room_id, "turn_limit", "turn limit reached")
            return "turn_limit"

        if int(room["stall_count"]) >= int(room["stall_limit"]):
            self._close_room(conn, room_id, "stall", "stall limit reached")
            return "stall"

        return None

    def _maybe_timeout(self, conn: Connection, room_id: str) -> None:
        room = self._require_room(conn, room_id)
        if room["status"] != "active":
            return
        if now_utc() >= ensure_utc(room["deadline_at"]):
            self._close_room(conn, room_id, "timeout", "room timeout reached")

    def _close_room(self, conn: Connection, room_id: str, stop_reason: str, stop_detail: str) -> None:
        room = self._require_room(conn, room_id)
        if room["status"] != "active":
            return
        ts = now_utc()
        conn.execute(
            update(rooms)
            .where(rooms.c.id == room_id)
            .values(status="closed", stop_reason=stop_reason, stop_detail=stop_detail, closed_at=ts)
        )
        self._emit_event(
            conn,
            room_id,
            "*",
            "status",
            {
                "status": "closed",
                "stop_reason": stop_reason,
                "stop_detail": stop_detail,
            },
        )
        self._emit_event(conn, room_id, "*", "result_ready", self._result(conn, room_id))

    def _result(self, conn: Connection, room_id: str) -> dict[str, Any]:
        room = self._room_snapshot(conn, room_id)
        transcript_rows = conn.execute(
            select(messages).where(messages.c.room_id == room_id).order_by(messages.c.id.asc())
        ).mappings().all()
        transcript = [
            {
                "id": int(row["id"]),
                "sender": row["sender"],
                "intent": row["intent"],
                "text": row["text"],
                "fills": row["fills_json"] or {},
                "facts": row["facts_json"] or [],
                "questions": row["questions_json"] or [],
                "expect_reply": bool(row["expect_reply"]),
                "meta": row["meta_json"] or {},
                "created_at": self._iso(row["created_at"]),
            }
            for row in transcript_rows
        ]
        required_total = len(room["required_fields"])
        required_filled = len(
            [key for key in room["required_fields"] if key in room.get("fields", {})]
        )
        summary = (
            f"Room ended with status={room['status']} reason={room['stop_reason']} "
            f"after {room['turn_count']} turns. Filled {required_filled}/{required_total} required fields."
        )
        return {
            "room_id": room_id,
            "status": room["status"],
            "stop_reason": room["stop_reason"],
            "stop_detail": room["stop_detail"],
            "turn_count": room["turn_count"],
            "required_total": required_total,
            "required_filled": required_filled,
            "fields": room["fields"],
            "transcript": transcript,
            "summary": summary,
        }

    def _upsert_owner_request(
        self,
        conn: Connection,
        room_id: str,
        participant: str,
        message_id: int,
        question_text: str,
    ) -> str:
        pending = conn.execute(
            select(owner_requests)
            .where(
                and_(
                    owner_requests.c.room_id == room_id,
                    owner_requests.c.participant == participant,
                    owner_requests.c.status == "open",
                )
            )
            .order_by(owner_requests.c.created_at.desc())
            .limit(1)
        ).mappings().first()
        if pending is not None:
            req_id = str(pending["id"])
            conn.execute(
                update(owner_requests)
                .where(owner_requests.c.id == req_id)
                .values(question_text=question_text, message_id=message_id)
            )
            return req_id

        req_id = f"oreq_{uuid.uuid4().hex[:12]}"
        conn.execute(
            insert(owner_requests).values(
                id=req_id,
                room_id=room_id,
                participant=participant,
                message_id=message_id,
                question_text=question_text,
                status="open",
                resolution_text=None,
                created_at=now_utc(),
                resolved_at=None,
            )
        )
        return req_id

    def _resolve_owner_request(self, conn: Connection, room_id: str, participant: str, answer_text: str) -> None:
        pending = conn.execute(
            select(owner_requests)
            .where(
                and_(
                    owner_requests.c.room_id == room_id,
                    owner_requests.c.participant == participant,
                    owner_requests.c.status == "open",
                )
            )
            .order_by(owner_requests.c.created_at.desc())
            .limit(1)
        ).mappings().first()
        if pending is None:
            return
        conn.execute(
            update(owner_requests)
            .where(owner_requests.c.id == pending["id"])
            .values(status="resolved", resolution_text=answer_text, resolved_at=now_utc())
        )

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return ensure_utc(value).isoformat()
