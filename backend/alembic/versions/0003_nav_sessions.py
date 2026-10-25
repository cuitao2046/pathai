# -*- coding: utf-8 -*-
"""导航会话与幂等表（docs/24 §8 API-06..09）。

Revision ID: 0003_nav_sessions
Revises: 0002_location_sequences
Create Date: 2026-10-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_nav_sessions"
down_revision = "0002_location_sequences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "nav_sessions",
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("map_version", sa.String(length=32), nullable=False),
        sa.Column("destination_place_id", sa.String(length=64), nullable=False),
        sa.Column("destination_name", sa.String(length=128), nullable=False),
        sa.Column("destination_floor_id", sa.String(length=16), nullable=False),
        sa.Column("user_mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("route_json", sa.Text(), nullable=False),
        sa.Column("current_index", sa.Integer(), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_response_json", sa.Text(), nullable=True),
        sa.Column("location_algorithm_version", sa.String(length=32), nullable=False),
        sa.Column("route_rules_version", sa.String(length=16), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
        sa.Column("ended_at_epoch", sa.Integer(), nullable=True),
        sa.Column("end_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["subject_id"], ["anonymous_subjects.id"]),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_nav_sessions_subject", "nav_sessions", ["subject_id"])
    op.create_table(
        "nav_idempotency_keys",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=40), nullable=False),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["anonymous_subjects.id"]),
        sa.PrimaryKeyConstraint("subject_id", "idempotency_key"),
    )


def downgrade() -> None:
    op.drop_table("nav_idempotency_keys")
    op.drop_index("ix_nav_sessions_subject", table_name="nav_sessions")
    op.drop_table("nav_sessions")
