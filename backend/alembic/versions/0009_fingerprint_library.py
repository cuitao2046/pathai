from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_fingerprint_library"
down_revision = "0008_calibration_console"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fingerprint_library_versions",
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
    )
    op.create_index(
        "ix_fingerprint_library_versions_site", "fingerprint_library_versions", ["site_id"]
    )

    op.create_table(
        "fingerprint_library_records",
        sa.Column("version", sa.String(length=40), primary_key=True),
        sa.Column("fp_id", sa.String(length=32), primary_key=True),
        sa.Column("collected_at", sa.String(length=40), primary_key=True),
        sa.Column("floor_id", sa.String(length=16), nullable=False),
        sa.Column("record_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_fingerprint_library_records_version", "fingerprint_library_records", ["version"]
    )

    op.create_table(
        "fingerprint_library_points",
        sa.Column("version", sa.String(length=40), primary_key=True),
        sa.Column("fp_id", sa.String(length=32), primary_key=True),
        sa.Column("floor_id", sa.String(length=16), nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("record_count", sa.Integer(), nullable=False),
        sa.Column("beacon_count", sa.Integer(), nullable=False),
        sa.Column("vector_json", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_fingerprint_library_points_version", "fingerprint_library_points", ["version"]
    )

    op.create_table(
        "fingerprint_library_pointer",
        sa.Column("site_id", sa.String(length=64), primary_key=True),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("fingerprint_library_pointer")
    op.drop_index(
        "ix_fingerprint_library_points_version", table_name="fingerprint_library_points"
    )
    op.drop_table("fingerprint_library_points")
    op.drop_index(
        "ix_fingerprint_library_records_version", table_name="fingerprint_library_records"
    )
    op.drop_table("fingerprint_library_records")
    op.drop_index(
        "ix_fingerprint_library_versions_site", table_name="fingerprint_library_versions"
    )
    op.drop_table("fingerprint_library_versions")
