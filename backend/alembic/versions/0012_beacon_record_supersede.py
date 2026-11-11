# -*- coding: utf-8 -*-
"""部署台账失效标记：同一 MAC 设备改绑后旧记录标记为已取代（docs/27 §6.8）。

Revision ID: 0012_beacon_record_supersede
Revises: 0011_fingerprint_idem_per_site
Create Date: 2026-11-11
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_beacon_record_supersede"
down_revision = "0011_fingerprint_idem_per_site"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 三列均允许为空：历史记录默认有效，不做猜测性回填。
    with op.batch_alter_table("beacon_deployment_records") as batch:
        batch.add_column(sa.Column("superseded_at_epoch", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("superseded_by_site_id", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("superseded_by_beacon_id", sa.String(length=32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("beacon_deployment_records") as batch:
        batch.drop_column("superseded_by_beacon_id")
        batch.drop_column("superseded_by_site_id")
        batch.drop_column("superseded_at_epoch")
