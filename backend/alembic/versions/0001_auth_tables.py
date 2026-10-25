# -*- coding: utf-8 -*-
"""匿名主体与访问令牌表（docs/24 §8 API-01 匿名主体规则）。

Revision ID: 0001_auth_tables
Revises:
Create Date: 2026-10-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_auth_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "anonymous_subjects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("openid_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=21), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("openid_hash", name="uq_anonymous_subjects_openid_hash"),
        sa.UniqueConstraint("user_id", name="uq_anonymous_subjects_user_id"),
    )
    op.create_table(
        "access_tokens",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("expires_at_epoch", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["subject_id"], ["anonymous_subjects.id"]),
        sa.PrimaryKeyConstraint("token_hash"),
    )
    op.create_index("ix_access_tokens_subject", "access_tokens", ["subject_id"])


def downgrade() -> None:
    op.drop_index("ix_access_tokens_subject", table_name="access_tokens")
    op.drop_table("access_tokens")
    op.drop_table("anonymous_subjects")
