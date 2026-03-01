from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)


metadata = MetaData()


rooms = Table(
    "rooms",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("topic", Text, nullable=False),
    Column("goal", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("stop_reason", String(64), nullable=True),
    Column("stop_detail", Text, nullable=True),
    Column("turn_limit", Integer, nullable=False),
    Column("turn_count", Integer, nullable=False),
    Column("stall_limit", Integer, nullable=False),
    Column("stall_count", Integer, nullable=False),
    Column("timeout_minutes", Integer, nullable=False),
    Column("deadline_at", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("closed_at", DateTime(timezone=True), nullable=True),
    Column("metadata_json", JSON, nullable=False),
    Column("host_token_hash", String(128), nullable=False, unique=True),
)

room_participants = Table(
    "room_participants",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("position", Integer, nullable=False),
    Column("invite_token_hash", String(128), nullable=False, unique=True),
    Column("client_name", String(120), nullable=True),
    Column("joined", Boolean, nullable=False),
    Column("online", Boolean, nullable=False),
    Column("done", Boolean, nullable=False),
    Column("waiting_owner", Boolean, nullable=False),
    Column("joined_at", DateTime(timezone=True), nullable=True),
    Column("last_seen_at", DateTime(timezone=True), nullable=True),
    UniqueConstraint("room_id", "name", name="uq_room_participant_name"),
)

room_required_fields = Table(
    "room_required_fields",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("field_key", String(120), nullable=False),
    UniqueConstraint("room_id", "field_key", name="uq_room_required_field"),
)

room_fields = Table(
    "room_fields",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("field_key", String(120), nullable=False),
    Column("value", Text, nullable=False),
    Column("updated_by", String(120), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("room_id", "field_key", name="uq_room_field"),
)

room_seen_texts = Table(
    "room_seen_texts",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("text_key", String(512), nullable=False),
    UniqueConstraint("room_id", "text_key", name="uq_room_seen_text"),
)

messages = Table(
    "messages",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("sender", String(120), nullable=False),
    Column("intent", String(24), nullable=False),
    Column("text", Text, nullable=False),
    Column("fills_json", JSON, nullable=False),
    Column("facts_json", JSON, nullable=False),
    Column("questions_json", JSON, nullable=False),
    Column("expect_reply", Boolean, nullable=False),
    Column("meta_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

events = Table(
    "events",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("audience", String(120), nullable=False),
    Column("type", String(40), nullable=False),
    Column("payload_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

owner_requests = Table(
    "owner_requests",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("room_id", String(64), ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
    Column("participant", String(120), nullable=False),
    Column("message_id", Integer, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
    Column("question_text", Text, nullable=False),
    Column("status", String(24), nullable=False),
    Column("resolution_text", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("resolved_at", DateTime(timezone=True), nullable=True),
)
