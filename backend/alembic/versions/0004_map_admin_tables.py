# -*- coding: utf-8 -*-
"""地图管理后台表：管理员账户、会话、草稿与审计事件（map-admin 技术设计 §4）。

Revision ID: 0004_map_admin_tables
Revises: 0003_nav_sessions
Create Date: 2026-10-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_map_admin_tables"
down_revision = "0003_nav_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_accounts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(length=128), nullable=False),
        sa.Column("password_salt", sa.String(length=64), nullable=False),
        sa.Column("hash_params", sa.String(length=64), nullable=False),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_table(
        "admin_sessions",
        sa.Column("token_hash", sa.String(length=64), primary_key=True),
        sa.Column("csrf_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("expires_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_table(
        "map_drafts",
        sa.Column("site_id", sa.String(length=64), primary_key=True),
        sa.Column("base_version", sa.String(length=32), nullable=False),
        sa.Column("patch_json", sa.Text(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("details_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("admin_audit_events")
    op.drop_table("map_drafts")
    op.drop_table("admin_sessions")
    op.drop_table("admin_accounts")
