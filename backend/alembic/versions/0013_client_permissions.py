"""Minimal permissions for silently authenticated WeChat subjects."""
from alembic import op
import sqlalchemy as sa
revision = "0013_client_permissions"
down_revision = "0012_beacon_record_supersede"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("client_permissions",
        sa.Column("subject_id", sa.Integer(), sa.ForeignKey("anonymous_subjects.id"), primary_key=True),
        sa.Column("is_tester", sa.Boolean(), nullable=False),
        sa.Column("can_apply_config", sa.Boolean(), nullable=False))
def downgrade():
    op.drop_table("client_permissions")
