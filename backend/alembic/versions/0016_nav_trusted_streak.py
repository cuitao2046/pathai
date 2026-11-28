"""Add nav session trusted streak for recovering hysteresis."""
from alembic import op
import sqlalchemy as sa

revision = "0016_nav_trusted_streak"
down_revision = "0015_field_records"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "nav_sessions",
        sa.Column("trusted_streak", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_column("nav_sessions", "trusted_streak")
