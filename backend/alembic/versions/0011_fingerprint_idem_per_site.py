# -*- coding: utf-8 -*-
"""指纹库内容幂等键按场所隔离。

content_sha256 的全局唯一约束改为 (site_id, content_sha256) 联合唯一：
同内容入库到不同场所各自成版本，不再跨场所判重。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_fingerprint_idem_per_site"
down_revision = "0010_fingerprint_retention"
branch_labels = None
depends_on = None

_UQ_NAME = "uq_fpl_versions_site_content"


def _source_table() -> sa.Table:
    """batch 重建用的现结构（无列级 unique；SQLite 列级 unique 是匿名索引）。"""
    return sa.Table(
        "fingerprint_library_versions",
        sa.MetaData(),
        sa.Column("version", sa.String(length=40), primary_key=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_file_name", sa.String(length=160), nullable=False),
        sa.Column("dataset", sa.String(length=32), nullable=True),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("fp_count", sa.Integer(), nullable=False),
        sa.Column("inserted_count", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("device_json", sa.Text(), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column("map_version", sa.String(length=32), nullable=True),
        sa.Column("grid_version", sa.String(length=40), nullable=True),
        sa.Column("params_version", sa.String(length=40), nullable=True),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("pruned_at_epoch", sa.Integer(), nullable=True),
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # SQLite：batch 整表重建——源定义去掉匿名列级 unique，重建时加联合唯一
        with op.batch_alter_table(
            "fingerprint_library_versions", copy_from=_source_table()
        ) as batch:
            batch.create_unique_constraint(_UQ_NAME, ["site_id", "content_sha256"])
        return
    op.drop_constraint(
        "fingerprint_library_versions_content_sha256_key",
        "fingerprint_library_versions",
        type_="unique",
    )
    op.create_unique_constraint(
        _UQ_NAME, "fingerprint_library_versions", ["site_id", "content_sha256"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        src = sa.Table(
            "fingerprint_library_versions",
            sa.MetaData(),
            sa.Column("version", sa.String(length=40), primary_key=True),
            sa.Column("site_id", sa.String(length=64), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False, unique=True),
            sa.Column("source_file_name", sa.String(length=160), nullable=False),
            sa.Column("dataset", sa.String(length=32), nullable=True),
            sa.Column("record_count", sa.Integer(), nullable=False),
            sa.Column("fp_count", sa.Integer(), nullable=False),
            sa.Column("inserted_count", sa.Integer(), nullable=False),
            sa.Column("conflict_count", sa.Integer(), nullable=False),
            sa.Column("operator", sa.String(length=64), nullable=True),
            sa.Column("device_json", sa.Text(), nullable=True),
            sa.Column("app_version", sa.String(length=32), nullable=True),
            sa.Column("map_version", sa.String(length=32), nullable=True),
            sa.Column("grid_version", sa.String(length=40), nullable=True),
            sa.Column("params_version", sa.String(length=40), nullable=True),
            sa.Column("created_at_epoch", sa.Integer(), nullable=False),
            sa.Column("pruned_at_epoch", sa.Integer(), nullable=True),
        )
        with op.batch_alter_table(
            "fingerprint_library_versions", copy_from=src
        ) as batch:
            batch.drop_constraint(_UQ_NAME, type_="unique")
        return
    op.drop_constraint(_UQ_NAME, "fingerprint_library_versions", type_="unique")
    op.create_unique_constraint(
        "fingerprint_library_versions_content_sha256_key",
        "fingerprint_library_versions",
        ["content_sha256"],
    )
