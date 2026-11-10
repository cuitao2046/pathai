from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_calibration_console"
down_revision = "0007_local_pairing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "calibration_samples",
        sa.Column("sample_id", sa.String(length=40), primary_key=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=160), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("sample_type", sa.String(length=48), nullable=False),
        sa.Column("deployable", sa.Boolean(), nullable=False),
        sa.Column("operator", sa.String(length=64), nullable=True),
        sa.Column("map_version", sa.String(length=32), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("stored_path", sa.String(length=512), nullable=False),
        sa.Column("uploaded_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_index("ix_calibration_samples_site", "calibration_samples", ["site_id"])

    op.create_table(
        "calibration_runs",
        sa.Column("run_id", sa.String(length=40), primary_key=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("sample_id", sa.String(length=40), nullable=False),
        sa.Column("sample_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("grid_version", sa.String(length=40), nullable=True),
        sa.Column("base_grid_version", sa.String(length=40), nullable=True),
        sa.Column("grid_staging_path", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_index("ix_calibration_runs_site", "calibration_runs", ["site_id"])
    op.create_index("ix_calibration_runs_sample", "calibration_runs", ["sample_id"])

    op.create_table(
        "location_param_versions",
        sa.Column("version", sa.String(length=40), primary_key=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("rssi_at_1m", sa.Float(), nullable=False),
        sa.Column("path_loss_n", sa.Float(), nullable=False),
        sa.Column("residual_reliable_m", sa.Float(), nullable=False),
        sa.Column("residual_degraded_m", sa.Float(), nullable=False),
        sa.Column("grid_version", sa.String(length=40), nullable=False),
        sa.Column("run_id", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_index("ix_location_param_versions_site", "location_param_versions", ["site_id"])

    op.create_table(
        "fingerprint_grid_versions",
        sa.Column("version", sa.String(length=40), primary_key=True),
        sa.Column("site_id", sa.String(length=64), nullable=False),
        sa.Column("base_grid_version", sa.String(length=40), nullable=False),
        sa.Column("map_version", sa.String(length=32), nullable=True),
        sa.Column("rssi_at_1m", sa.Float(), nullable=False),
        sa.Column("path_loss_n", sa.Float(), nullable=False),
        sa.Column("overlay_path", sa.String(length=512), nullable=False),
        sa.Column("manifest_path", sa.String(length=512), nullable=False),
        sa.Column("point_count", sa.Integer(), nullable=False),
        sa.Column("collectable_count", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
    )
    op.create_index("ix_fingerprint_grid_versions_site", "fingerprint_grid_versions", ["site_id"])

    op.create_table(
        "calibration_active_pointer",
        sa.Column("site_id", sa.String(length=64), primary_key=True),
        sa.Column("param_version", sa.String(length=40), nullable=False),
        sa.Column("grid_version", sa.String(length=40), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("calibration_active_pointer")
    op.drop_index("ix_fingerprint_grid_versions_site", table_name="fingerprint_grid_versions")
    op.drop_table("fingerprint_grid_versions")
    op.drop_index("ix_location_param_versions_site", table_name="location_param_versions")
    op.drop_table("location_param_versions")
    op.drop_index("ix_calibration_runs_sample", table_name="calibration_runs")
    op.drop_index("ix_calibration_runs_site", table_name="calibration_runs")
    op.drop_table("calibration_runs")
    op.drop_index("ix_calibration_samples_site", table_name="calibration_samples")
    op.drop_table("calibration_samples")
