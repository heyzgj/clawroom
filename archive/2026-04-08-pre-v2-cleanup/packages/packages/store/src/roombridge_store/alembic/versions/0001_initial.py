"""initial clawroom schema

Revision ID: 0001_initial
Revises: None
Create Date: 2026-02-27 00:00:00
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rooms",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("topic", sa.Text(), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stop_reason", sa.String(length=64), nullable=True),
        sa.Column("stop_detail", sa.Text(), nullable=True),
        sa.Column("turn_limit", sa.Integer(), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False),
        sa.Column("stall_limit", sa.Integer(), nullable=False),
        sa.Column("stall_count", sa.Integer(), nullable=False),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("host_token_hash", sa.String(length=128), nullable=False, unique=True),
    )

    op.create_table(
        "room_participants",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("invite_token_hash", sa.String(length=128), nullable=False, unique=True),
        sa.Column("client_name", sa.String(length=120), nullable=True),
        sa.Column("joined", sa.Boolean(), nullable=False),
        sa.Column("online", sa.Boolean(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("waiting_owner", sa.Boolean(), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("room_id", "name", name="uq_room_participant_name"),
    )

    op.create_table(
        "room_required_fields",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.UniqueConstraint("room_id", "field_key", name="uq_room_required_field"),
    )

    op.create_table(
        "room_fields",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("field_key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(length=120), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("room_id", "field_key", name="uq_room_field"),
    )

    op.create_table(
        "room_seen_texts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("text_key", sa.String(length=512), nullable=False),
        sa.UniqueConstraint("room_id", "text_key", name="uq_room_seen_text"),
    )

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender", sa.String(length=120), nullable=False),
        sa.Column("intent", sa.String(length=24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("fills_json", sa.JSON(), nullable=False),
        sa.Column("facts_json", sa.JSON(), nullable=False),
        sa.Column("questions_json", sa.JSON(), nullable=False),
        sa.Column("expect_reply", sa.Boolean(), nullable=False),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("audience", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "owner_requests",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("room_id", sa.String(length=64), sa.ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False),
        sa.Column("participant", sa.String(length=120), nullable=False),
        sa.Column("message_id", sa.Integer(), sa.ForeignKey("messages.id", ondelete="SET NULL"), nullable=True),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("resolution_text", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("owner_requests")
    op.drop_table("events")
    op.drop_table("messages")
    op.drop_table("room_seen_texts")
    op.drop_table("room_fields")
    op.drop_table("room_required_fields")
    op.drop_table("room_participants")
    op.drop_table("rooms")
