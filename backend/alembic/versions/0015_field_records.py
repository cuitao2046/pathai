"""Add immutable field experiment records."""
from alembic import op
import sqlalchemy as sa

revision = "0015_field_records"
down_revision = "0014_nav_policy"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nav_sessions", sa.Column("experiment_json", sa.Text(), nullable=True))
    op.create_table(
        "field_records",
        sa.Column("kind", sa.String(12), primary_key=True),
        sa.Column("record_id", sa.String(128), primary_key=True),
        sa.Column("operator", sa.String(64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade():
    op.drop_table("field_records")
    op.drop_column("nav_sessions", "experiment_json")
