# -*- coding: utf-8 -*-
"""信标配置与真实部署台账表（docs/27 §5.2 / §6.8）。

Revision ID: 0006_beacon_deployment
Revises: 0005_location_params_version
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_beacon_deployment"
down_revision = "0005_location_params_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "beacon_deployment_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("beacon_id", sa.String(length=32), nullable=False),
        sa.Column("map_version", sa.String(length=32), nullable=False),
        sa.Column("device_mac", sa.String(length=32), nullable=True),
        sa.Column("device_name", sa.String(length=16), nullable=True),
        sa.Column("name_write", sa.String(length=16), nullable=True),
        sa.Column("written_json", sa.Text(), nullable=False),
        sa.Column("verify_json", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("configured_at_epoch", sa.Integer(), nullable=True),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
        sa.UniqueConstraint("site_id", "beacon_id", name="uq_beacon_record_site_beacon"),
    )
    op.create_index("ix_beacon_records_site", "beacon_deployment_records", ["site_id"])

    op.create_table(
        "beacon_provisioning_rounds",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("map_version", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("recorded", sa.Integer(), nullable=False),
        sa.Column("verified", sa.Integer(), nullable=False),
        sa.Column("summary_json", sa.Text(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_index("ix_beacon_rounds_site", "beacon_provisioning_rounds", ["site_id"])


def downgrade() -> None:
    op.drop_index("ix_beacon_rounds_site", table_name="beacon_provisioning_rounds")
    op.drop_table("beacon_provisioning_rounds")
    op.drop_index("ix_beacon_records_site", table_name="beacon_deployment_records")
    op.drop_table("beacon_deployment_records")
