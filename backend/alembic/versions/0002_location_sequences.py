# -*- coding: utf-8 -*-
"""定位批次序号表（docs/24 §8 API-05 STALE_LOCATION_BATCH）。

Revision ID: 0002_location_sequences
Revises: 0001_auth_tables
Create Date: 2026-10-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_location_sequences"
down_revision = "0001_auth_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "location_sequences",
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["anonymous_subjects.id"]),
        sa.PrimaryKeyConstraint("subject_id", "site_id"),
    )


def downgrade() -> None:
    op.drop_table("location_sequences")
