"""Store anonymized analytics aggregates with retention metadata.

Revision ID: 0003_analytics_aggregates
Revises: 0002_navigation_progress
"""
from alembic import op
import sqlalchemy as sa

revision = "0003_analytics_aggregates"
down_revision = "0002_navigation_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analytics_aggregates",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("metric", sa.String(length=80), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("aggregate_count", sa.Integer(), nullable=False),
        sa.Column("anonymized_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("aggregate_count >= 0", name="ck_analytics_aggregate_count_nonnegative"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analytics_aggregates_metric", "analytics_aggregates", ["metric"])
    op.create_index("ix_analytics_aggregates_anonymized_at", "analytics_aggregates", ["anonymized_at"])


def downgrade() -> None:
    op.drop_index("ix_analytics_aggregates_anonymized_at", table_name="analytics_aggregates")
    op.drop_index("ix_analytics_aggregates_metric", table_name="analytics_aggregates")
    op.drop_table("analytics_aggregates")
