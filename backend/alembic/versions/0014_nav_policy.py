"""Per-site nav thresholds editable from the mini-program settings page."""
from alembic import op
import sqlalchemy as sa

revision = "0014_nav_policy"
down_revision = "0013_client_permissions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "nav_policy",
        sa.Column("site_id", sa.String(length=64), primary_key=True),
        sa.Column("arrival_radius_m", sa.Float(), nullable=False),
        sa.Column("turn_prompt_distance_m", sa.Float(), nullable=False),
        sa.Column("updated_at_epoch", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=64), nullable=True),
    )


def downgrade():
    op.drop_table("nav_policy")
