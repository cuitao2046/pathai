"""指纹库：版本保留策略与删除语义（计数/模式/明细裁剪标记）。

只加列，不改既有列；已有版本行由 server_default 补默认值（merge / 0 / 未裁剪）。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_fingerprint_retention"
down_revision = "0009_fingerprint_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("updated_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("deleted_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("retained_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("previous_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column(
            "ingest_mode", sa.String(length=24), nullable=False, server_default="merge"
        ),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("replace_floors_json", sa.Text(), nullable=True),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column(
            "details_pruned", sa.Boolean(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "fingerprint_library_versions",
        sa.Column("pruned_at_epoch", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fingerprint_library_versions", "pruned_at_epoch")
    op.drop_column("fingerprint_library_versions", "details_pruned")
    op.drop_column("fingerprint_library_versions", "replace_floors_json")
    op.drop_column("fingerprint_library_versions", "ingest_mode")
    op.drop_column("fingerprint_library_versions", "previous_count")
    op.drop_column("fingerprint_library_versions", "retained_count")
    op.drop_column("fingerprint_library_versions", "deleted_count")
    op.drop_column("fingerprint_library_versions", "updated_count")
