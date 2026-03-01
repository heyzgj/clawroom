from __future__ import annotations

import json
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def norm(text: str) -> str:
    return " ".join(text.strip().lower().split())


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


@dataclass(slots=True)
class MessageData:
    intent: str
    text: str
    fills: dict[str, str]
    facts: list[str]
    questions: list[str]
    wants_reply: bool
    metadata: dict[str, Any]


class RoomStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._write_lock = threading.RLock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def init(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                  id TEXT PRIMARY KEY,
                  topic TEXT NOT NULL,
                  goal TEXT NOT NULL,
                  status TEXT NOT NULL,
                  stop_reason TEXT NOT NULL DEFAULT 'none',
                  stop_detail TEXT,
                  turn_limit INTEGER NOT NULL,
                  timeout_minutes INTEGER NOT NULL,
                  stall_limit INTEGER NOT NULL,
                  turn_count INTEGER NOT NULL DEFAULT 0,
                  stall_count INTEGER NOT NULL DEFAULT 0,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  deadline_at TEXT NOT NULL,
                  closed_at TEXT,
                  host_token TEXT NOT NULL UNIQUE
                );

                CREATE TABLE IF NOT EXISTS room_participants (
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  name TEXT NOT NULL,
                  position INTEGER NOT NULL,
                  invite_token TEXT NOT NULL UNIQUE,
                  client_name TEXT,
                  joined INTEGER NOT NULL DEFAULT 0,
                  online INTEGER NOT NULL DEFAULT 0,
                  done INTEGER NOT NULL DEFAULT 0,
                  joined_at TEXT,
                  last_seen_at TEXT,
                  PRIMARY KEY (room_id, name)
                );

                CREATE TABLE IF NOT EXISTS room_required_fields (
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  field_key TEXT NOT NULL,
                  position INTEGER NOT NULL,
                  PRIMARY KEY (room_id, field_key)
                );

                CREATE TABLE IF NOT EXISTS room_fields (
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  field_key TEXT NOT NULL,
                  value TEXT NOT NULL,
                  updated_by TEXT,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY (room_id, field_key)
                );

                CREATE TABLE IF NOT EXISTS room_seen_texts (
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  text_key TEXT NOT NULL,
                  PRIMARY KEY (room_id, text_key)
                );

                CREATE TABLE IF NOT EXISTS messages (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  sender TEXT NOT NULL,
                  intent TEXT NOT NULL,
                  text TEXT NOT NULL,
                  fills_json TEXT NOT NULL DEFAULT '{}',
                  facts_json TEXT NOT NULL DEFAULT '[]',
                  questions_json TEXT NOT NULL DEFAULT '[]',
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  wants_reply INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  room_id TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
                  audience TEXT NOT NULL,
                  type TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_events_room_id_id ON events(room_id, id);
                CREATE INDEX IF NOT EXISTS idx_messages_room_id_id ON messages(room_id, id);
                CREATE INDEX IF NOT EXISTS idx_room_participants_token ON room_participants(invite_token);
                """
            )

    def create_room(self, data: RoomCreateData) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            now = utc_now()
            room_id = f"room_{uuid4().hex[:10]}"
            host_token = secrets.token_urlsafe(24)
            deadline = now + timedelta(minutes=data.timeout_minutes)

            conn.execute(
                """
                INSERT INTO rooms (
                  id, topic, goal, status, turn_limit, timeout_minutes, stall_limit,
                  metadata_json, created_at, deadline_at, host_token
                ) VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    data.topic,
                    data.goal,
                    data.turn_limit,
                    data.timeout_minutes,
                    data.stall_limit,
                    json.dumps(data.metadata, ensure_ascii=False),
                    iso(now),
                    iso(deadline),
                    host_token,
                ),
            )

            invite_tokens: dict[str, str] = {}
            for idx, participant in enumerate(data.participants):
                token = secrets.token_urlsafe(24)
                invite_tokens[participant] = token
                conn.execute(
                    """
                    INSERT INTO room_participants (room_id, name, position, invite_token)
                    VALUES (?, ?, ?, ?)
                    """,
                    (room_id, participant, idx, token),
                )

            for idx, field_key in enumerate(data.required_fields):
                conn.execute(
                    """
                    INSERT INTO room_required_fields (room_id, field_key, position)
                    VALUES (?, ?, ?)
                    """,
                    (room_id, field_key, idx),
                )

            self._emit_event(
                conn,
                room_id=room_id,
                audience="*",
                type_="status",
                payload={
                    "status": "active",
                    "stop_reason": "none",
                    "turn_count": 0,
                    "stall_count": 0,
                    "kind": "room_created",
                },
            )

            room = self._room_snapshot(conn, room_id)
            return {
                "room": room,
                "host_token": host_token,
                "invite_tokens": invite_tokens,
            }

    def inspect_invite(self, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            room, participant = self._room_and_participant_by_token(conn, token)
            self._maybe_timeout(conn, room["id"])
            room = self._room_snapshot(conn, room["id"])
            return {"room": room, "participant": participant["name"]}

    def get_room_for_participant(self, room_id: str, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            return self._room_snapshot(conn, room_id)

    def get_room_for_host(self, room_id: str, host_token: str) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            return self._room_snapshot(conn, room_id)

    def join_room(self, room_id: str, token: str, client_name: str | None) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            room = self._require_room(conn, room_id)
            if room["status"] == "canceled":
                raise ValueError("room is canceled")
            now = iso(utc_now())
            was_online = bool(participant["online"])
            had_joined = bool(participant["joined"])
            effective_client_name = client_name.strip() if client_name and client_name.strip() else participant["name"]
            conn.execute(
                """
                UPDATE room_participants
                SET client_name = ?, joined = 1, online = 1,
                    joined_at = COALESCE(joined_at, ?), last_seen_at = ?
                WHERE room_id = ? AND name = ?
                """,
                (effective_client_name, now, now, room_id, participant["name"]),
            )
            if not was_online:
                self._emit_event(
                    conn,
                    room_id=room_id,
                    audience="*",
                    type_="system",
                    payload={
                        "kind": "join",
                        "participant": participant["name"],
                        "client_name": effective_client_name,
                        "rejoin": had_joined,
                    },
                )
            return {
                "participant": participant["name"],
                "client_name": effective_client_name,
                "room": self._room_snapshot(conn, room_id),
            }

    def leave_room(self, room_id: str, token: str, reason: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            was_online = bool(participant["online"])
            conn.execute(
                "UPDATE room_participants SET online = 0, last_seen_at = ? WHERE room_id = ? AND name = ?",
                (iso(utc_now()), room_id, participant["name"]),
            )
            if was_online:
                self._emit_event(
                    conn,
                    room_id=room_id,
                    audience="*",
                    type_="system",
                    payload={
                        "kind": "leave",
                        "participant": participant["name"],
                        "client_name": participant["client_name"] or participant["name"],
                        "reason": reason,
                    },
                )
            return {
                "participant": participant["name"],
                "was_online": was_online,
                "room": self._room_snapshot(conn, room_id),
            }

    def close_room(self, room_id: str, token: str, reason: str) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            self._close_room(
                conn,
                room_id=room_id,
                new_status="closed",
                stop_reason="manual",
                stop_detail=f"{participant['name']}: {reason}",
            )
            return {"room": self._room_snapshot(conn, room_id)}

    def post_message(self, room_id: str, token: str, msg: MessageData) -> dict[str, Any]:
        with self._write_lock, self._connect() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            room = self._require_room(conn, room_id)
            if room["status"] != "active":
                raise RuntimeError(f"room is not active ({room['status']})")

            sender = participant["name"]
            now = iso(utc_now())
            clean_fills = {k.strip(): v.strip() for k, v in msg.fills.items() if k.strip() and v.strip()}
            clean_facts = [f.strip() for f in msg.facts if f.strip()]
            clean_questions = [q.strip() for q in msg.questions if q.strip()]

            conn.execute(
                """
                INSERT INTO messages (
                  room_id, sender, intent, text, fills_json, facts_json, questions_json, metadata_json, wants_reply, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    sender,
                    msg.intent,
                    msg.text.strip(),
                    json.dumps(clean_fills, ensure_ascii=False),
                    json.dumps(clean_facts, ensure_ascii=False),
                    json.dumps(clean_questions, ensure_ascii=False),
                    json.dumps(msg.metadata, ensure_ascii=False),
                    1 if msg.wants_reply else 0,
                    now,
                ),
            )
            message_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

            conn.execute(
                "UPDATE room_participants SET online = 1, joined = 1, last_seen_at = ?, client_name = COALESCE(client_name, name) WHERE room_id = ? AND name = ?",
                (now, room_id, sender),
            )

            new_field_count = 0
            for field_key, value in clean_fills.items():
                cur = conn.execute(
                    "SELECT value FROM room_fields WHERE room_id = ? AND field_key = ?",
                    (room_id, field_key),
                )
                existing = cur.fetchone()
                if existing is None:
                    conn.execute(
                        """
                        INSERT INTO room_fields (room_id, field_key, value, updated_by, updated_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (room_id, field_key, value, sender, now),
                    )
                    new_field_count += 1
                elif str(existing["value"]) != value:
                    conn.execute(
                        """
                        UPDATE room_fields SET value = ?, updated_by = ?, updated_at = ?
                        WHERE room_id = ? AND field_key = ?
                        """,
                        (value, sender, now, room_id, field_key),
                    )
                    new_field_count += 1

            text_key = norm(msg.text)
            is_new_text = False
            if text_key:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO room_seen_texts (room_id, text_key) VALUES (?, ?)",
                    (room_id, text_key),
                )
                is_new_text = cur.rowcount > 0

            structured_progress = bool(new_field_count or clean_facts)
            progress = structured_progress or is_new_text

            conn.execute(
                "UPDATE rooms SET turn_count = turn_count + 1 WHERE id = ?",
                (room_id,),
            )
            if progress:
                conn.execute("UPDATE rooms SET stall_count = 0 WHERE id = ?", (room_id,))
            elif msg.intent not in {"DONE", "NEED_HUMAN"}:
                conn.execute("UPDATE rooms SET stall_count = stall_count + 1 WHERE id = ?", (room_id,))

            if msg.intent == "DONE":
                conn.execute(
                    "UPDATE room_participants SET done = 1 WHERE room_id = ? AND name = ?",
                    (room_id, sender),
                )

            close_trigger = self._evaluate_rules(conn, room_id, sender, msg.intent)

            relay_recipients: list[str] = []
            room_after_rules = self._require_room(conn, room_id)
            if room_after_rules["status"] == "active" and msg.wants_reply:
                recipients = conn.execute(
                    """
                    SELECT name FROM room_participants
                    WHERE room_id = ? AND name <> ?
                    ORDER BY position ASC
                    """,
                    (room_id, sender),
                ).fetchall()
                message_payload = self._message_row(conn, message_id)
                for row in recipients:
                    recipient_name = str(row["name"])
                    relay_recipients.append(recipient_name)
                    self._emit_event(
                        conn,
                        room_id=room_id,
                        audience=recipient_name,
                        type_="relay",
                        payload={
                            "from": sender,
                            "message": message_payload,
                        },
                    )

            room_snapshot = self._room_snapshot(conn, room_id)
            return {
                "message_id": message_id,
                "sender": sender,
                "progress": {
                    "structured": structured_progress,
                    "new_text": is_new_text,
                    "new_fields": new_field_count,
                    "has_progress": progress,
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
        with self._connect() as conn:
            participant = self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            room = self._room_snapshot(conn, room_id)
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE room_id = ? AND id > ? AND (audience = '*' OR audience = ?)
                ORDER BY id ASC
                LIMIT ?
                """,
                (room_id, after, participant["name"], limit),
            ).fetchall()
            events = [self._event_row(row) for row in rows]
            next_cursor = events[-1]["id"] if events else after
            return {"room": room, "events": events, "next_cursor": next_cursor}

    def monitor_events(self, room_id: str, host_token: str, after: int, limit: int) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            room = self._room_snapshot(conn, room_id)
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE room_id = ? AND id > ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (room_id, after, limit),
            ).fetchall()
            events = [self._event_row(row) for row in rows]
            next_cursor = events[-1]["id"] if events else after
            return {"room": room, "events": events, "next_cursor": next_cursor}

    def participant_result(self, room_id: str, token: str) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_participant(conn, room_id, token)
            self._maybe_timeout(conn, room_id)
            return self._result(conn, room_id)

    def monitor_result(self, room_id: str, host_token: str) -> dict[str, Any]:
        with self._connect() as conn:
            self._require_host(conn, room_id, host_token)
            self._maybe_timeout(conn, room_id)
            return self._result(conn, room_id)

    # ---- internals

    def _emit_event(self, conn: sqlite3.Connection, room_id: str, audience: str, type_: str, payload: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO events (room_id, audience, type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (room_id, audience, type_, json.dumps(payload, ensure_ascii=False), iso(utc_now())),
        )

    def _event_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "ts": row["created_at"],
            "audience": row["audience"],
            "type": row["type"],
            "payload": json.loads(row["payload_json"]),
        }

    def _message_row(self, conn: sqlite3.Connection, message_id: int) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
        if not row:
            raise LookupError("message not found")
        return {
            "id": int(row["id"]),
            "ts": row["created_at"],
            "sender": row["sender"],
            "intent": row["intent"],
            "text": row["text"],
            "fills": json.loads(row["fills_json"]),
            "facts": json.loads(row["facts_json"]),
            "questions": json.loads(row["questions_json"]),
            "metadata": json.loads(row["metadata_json"]),
            "wants_reply": bool(row["wants_reply"]),
        }

    def _require_room(self, conn: sqlite3.Connection, room_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        if not row:
            raise LookupError("room not found")
        return row

    def _room_and_participant_by_token(self, conn: sqlite3.Connection, token: str) -> tuple[sqlite3.Row, sqlite3.Row]:
        row = conn.execute(
            """
            SELECT rp.*, r.id AS room_id_ref, r.topic AS room_topic
            FROM room_participants rp
            JOIN rooms r ON r.id = rp.room_id
            WHERE rp.invite_token = ?
            """,
            (token,),
        ).fetchone()
        if not row:
            raise PermissionError("invalid invite token")
        room = conn.execute("SELECT * FROM rooms WHERE id = ?", (row["room_id"],)).fetchone()
        if not room:
            raise LookupError("room not found")
        return room, row

    def _require_participant(self, conn: sqlite3.Connection, room_id: str, token: str) -> sqlite3.Row:
        row = conn.execute(
            """
            SELECT * FROM room_participants
            WHERE room_id = ? AND invite_token = ?
            """,
            (room_id, token),
        ).fetchone()
        if not row:
            raise PermissionError("invalid invite token")
        return row

    def _require_host(self, conn: sqlite3.Connection, room_id: str, host_token: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM rooms WHERE id = ? AND host_token = ?",
            (room_id, host_token),
        ).fetchone()
        if not row:
            raise PermissionError("invalid host token")
        return row

    def _room_snapshot(self, conn: sqlite3.Connection, room_id: str) -> dict[str, Any]:
        room = self._require_room(conn, room_id)
        participants = conn.execute(
            """
            SELECT name, position, client_name, joined, online, done, joined_at, last_seen_at
            FROM room_participants
            WHERE room_id = ?
            ORDER BY position ASC
            """,
            (room_id,),
        ).fetchall()
        required_fields = conn.execute(
            """
            SELECT field_key FROM room_required_fields
            WHERE room_id = ?
            ORDER BY position ASC
            """,
            (room_id,),
        ).fetchall()
        fields = conn.execute(
            """
            SELECT field_key, value, updated_by, updated_at
            FROM room_fields
            WHERE room_id = ?
            ORDER BY field_key ASC
            """,
            (room_id,),
        ).fetchall()

        fields_map = {
            str(row["field_key"]): {
                "value": str(row["value"]),
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
            for row in fields
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
            "created_at": room["created_at"],
            "deadline_at": room["deadline_at"],
            "closed_at": room["closed_at"],
            "metadata": json.loads(room["metadata_json"] or "{}"),
            "participants": [
                {
                    "name": str(p["name"]),
                    "client_name": p["client_name"],
                    "joined": bool(p["joined"]),
                    "online": bool(p["online"]),
                    "done": bool(p["done"]),
                    "joined_at": p["joined_at"],
                    "last_seen_at": p["last_seen_at"],
                }
                for p in participants
            ],
            "required_fields": [str(r["field_key"]) for r in required_fields],
            "fields": fields_map,
        }

    def _evaluate_rules(self, conn: sqlite3.Connection, room_id: str, sender: str, intent: str) -> str | None:
        room = self._require_room(conn, room_id)

        if intent == "NEED_HUMAN":
            self._close_room(conn, room_id, "paused", "need_human", f"{sender} requested human input")
            return "need_human"

        required_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM room_required_fields WHERE room_id = ?",
                (room_id,),
            ).fetchone()[0]
        )
        if required_total > 0:
            filled_required = int(
                conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM room_required_fields rf
                    JOIN room_fields f ON f.room_id = rf.room_id AND f.field_key = rf.field_key
                    WHERE rf.room_id = ?
                    """,
                    (room_id,),
                ).fetchone()[0]
            )
            if filled_required >= required_total:
                self._close_room(conn, room_id, "closed", "goal_done", "all required fields filled")
                return "goal_done"

        participant_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM room_participants WHERE room_id = ?",
                (room_id,),
            ).fetchone()[0]
        )
        done_total = int(
            conn.execute(
                "SELECT COUNT(*) FROM room_participants WHERE room_id = ? AND done = 1",
                (room_id,),
            ).fetchone()[0]
        )
        if participant_total > 0 and done_total >= participant_total:
            self._close_room(conn, room_id, "closed", "mutual_done", "all participants sent DONE")
            return "mutual_done"

        if utc_now() >= parse_ts(room["deadline_at"]):
            self._close_room(conn, room_id, "closed", "timeout", "room timeout reached")
            return "timeout"

        room = self._require_room(conn, room_id)
        if int(room["turn_count"]) >= int(room["turn_limit"]):
            self._close_room(conn, room_id, "closed", "turn_limit", "turn limit reached")
            return "turn_limit"

        if int(room["stall_count"]) >= int(room["stall_limit"]):
            self._close_room(conn, room_id, "closed", "stall", "stall limit reached")
            return "stall"

        return None

    def _maybe_timeout(self, conn: sqlite3.Connection, room_id: str) -> None:
        room = self._require_room(conn, room_id)
        if room["status"] != "active":
            return
        if utc_now() >= parse_ts(room["deadline_at"]):
            self._close_room(conn, room_id, "closed", "timeout", "room timeout reached")

    def _close_room(
        self,
        conn: sqlite3.Connection,
        room_id: str,
        new_status: str,
        stop_reason: str,
        stop_detail: str,
    ) -> None:
        room = self._require_room(conn, room_id)
        if room["status"] != "active":
            return

        now = iso(utc_now())
        cur = conn.execute(
            """
            UPDATE rooms
            SET status = ?, stop_reason = ?, stop_detail = ?, closed_at = ?
            WHERE id = ? AND status = 'active'
            """,
            (new_status, stop_reason, stop_detail, now, room_id),
        )
        if cur.rowcount == 0:
            return

        online_rows = conn.execute(
            "SELECT name, COALESCE(client_name, name) AS client_name FROM room_participants WHERE room_id = ? AND online = 1",
            (room_id,),
        ).fetchall()
        conn.execute(
            "UPDATE room_participants SET online = 0, last_seen_at = ? WHERE room_id = ? AND online = 1",
            (now, room_id),
        )

        for row in online_rows:
            self._emit_event(
                conn,
                room_id=room_id,
                audience="*",
                type_="system",
                payload={
                    "kind": "leave",
                    "participant": row["name"],
                    "client_name": row["client_name"],
                    "reason": "room_closed",
                },
            )

        room_after = self._require_room(conn, room_id)
        self._emit_event(
            conn,
            room_id=room_id,
            audience="*",
            type_="status",
            payload={
                "status": room_after["status"],
                "stop_reason": room_after["stop_reason"],
                "stop_detail": room_after["stop_detail"],
                "turn_count": int(room_after["turn_count"]),
                "stall_count": int(room_after["stall_count"]),
            },
        )
        self._emit_event(
            conn,
            room_id=room_id,
            audience="*",
            type_="system",
            payload={"kind": "result_ready"},
        )

    def _result(self, conn: sqlite3.Connection, room_id: str) -> dict[str, Any]:
        room = self._room_snapshot(conn, room_id)
        msgs = conn.execute(
            """
            SELECT * FROM messages
            WHERE room_id = ?
            ORDER BY id ASC
            """,
            (room_id,),
        ).fetchall()
        transcript = [self._message_row(conn, int(row["id"])) for row in msgs]
        filled_required = [
            key for key in room["required_fields"] if key in room["fields"]
        ]
        summary = (
            f"Room ended with status={room['status']} reason={room['stop_reason']} "
            f"after {room['turn_count']} turns. Filled {len(filled_required)}/{len(room['required_fields'])} required fields."
        )
        return {
            "room": room,
            "summary": summary,
            "transcript": transcript,
            "filled_fields": {k: v["value"] for k, v in room["fields"].items()},
            "missing_required_fields": [k for k in room["required_fields"] if k not in room["fields"]],
        }
