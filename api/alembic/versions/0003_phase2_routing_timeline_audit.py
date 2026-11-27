"""Phase 2 audit update: timeline event_source/linked_evidence_ids and routing source audit.

Revision ID: 0003_phase2_routing_timeline_audit
Revises: 0002_phase2_payment_cases
Create Date: 2026-11-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_phase2_audit_updates"
down_revision: Union[str, None] = "0002_phase2_payment_cases"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add event_source, linked_evidence_ids, and routing what_source_confirms."""
    op.add_column(
        "case_timeline_events",
        sa.Column("event_source", sa.String(length=16), nullable=False, server_default="system"),
    )
    op.add_column(
        "case_timeline_events",
        sa.Column("linked_evidence_ids", sa.JSON(), nullable=True),
    )
    op.add_column(
        "routing_channels",
        sa.Column("what_source_confirms", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    """Remove Phase 2 audit columns."""
    op.drop_column("routing_channels", "what_source_confirms")
    op.drop_column("case_timeline_events", "linked_evidence_ids")
    op.drop_column("case_timeline_events", "event_source")
