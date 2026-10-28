# -*- coding: utf-8 -*-
"""导航会话固化定位参数版本（docs/28 §5.4）。

Revision ID: 0005_location_params_version
Revises: 0004_map_admin_tables
Create Date: 2026-10-28
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_location_params_version"
down_revision = "0004_map_admin_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 存量会话无参数版本记录，统一回落 "unset"（工程占位期）
    op.add_column(
        "nav_sessions",
        sa.Column(
            "location_params_version",
            sa.String(length=32),
            nullable=False,
            server_default="unset",
        ),
    )


def downgrade() -> None:
    op.drop_column("nav_sessions", "location_params_version")
