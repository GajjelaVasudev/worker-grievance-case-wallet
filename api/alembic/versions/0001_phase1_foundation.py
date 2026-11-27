"""Phase 1 foundation: worker_profiles table for single local wallet.

Revision ID: 0001_phase1_foundation
Revises:
Create Date: 2026-11-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001_phase1_foundation"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create initial worker_profiles table."""
    op.create_table(
        "worker_profiles",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("display_label", sa.String(length=120), nullable=False),
        sa.Column("preferred_language", sa.String(length=32), nullable=False, server_default="en-IN"),
        sa.Column("auth_subject", sa.String(length=128), nullable=True, unique=True),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    """Drop worker_profiles table."""
    op.drop_table("worker_profiles")
