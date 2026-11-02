from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_local_pairing"
down_revision = "0006_beacon_deployment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "local_device_identities",
        sa.Column("slot", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(length=40), nullable=False, unique=True),
    )
    op.create_table(
        "local_pairings",
        sa.Column("pair_id", sa.String(length=40), primary_key=True),
        sa.Column("key_b64", sa.String(length=43), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("expires_at_epoch", sa.Integer(), nullable=False),
        sa.Column("created_at_epoch", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("local_pairings")
    op.drop_table("local_device_identities")
