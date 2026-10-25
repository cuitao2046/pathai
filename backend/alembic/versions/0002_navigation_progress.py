"""Persist arrival dwell and off-route state.

Revision ID: 0002_navigation_progress
Revises: 0001_initial
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_navigation_progress"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("navigation_sessions")}
    if "arrival_started_at" not in columns:
        op.add_column("navigation_sessions", sa.Column("arrival_started_at", sa.DateTime(timezone=True), nullable=True))
    if "arrival_last_sample_at" not in columns:
        op.add_column("navigation_sessions", sa.Column("arrival_last_sample_at", sa.DateTime(timezone=True), nullable=True))
    if "off_route_count" not in columns:
        op.add_column("navigation_sessions", sa.Column("off_route_count", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("navigation_sessions")}
    for name in ("off_route_count", "arrival_last_sample_at", "arrival_started_at"):
        if name in columns:
            op.drop_column("navigation_sessions", name)
