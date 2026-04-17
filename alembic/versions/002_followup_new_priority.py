"""Add new_priority column to followups table.

Revision ID: 002
Revises: 001
Create Date: 2026-03-27
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "followups",
        sa.Column("new_priority", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("followups", "new_priority")
