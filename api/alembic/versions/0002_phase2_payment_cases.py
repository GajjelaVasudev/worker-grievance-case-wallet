"""Phase 2 tables: grievance_cases, evidence_items, case_timeline_events, routing_channels.

Revision ID: 0002_phase2_payment_cases
Revises: 0001_phase1_foundation
Create Date: 2026-11-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_phase2_payment_cases"
down_revision: Union[str, None] = "0001_phase1_foundation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Phase 2 grievance case, evidence, timeline, and routing tables."""
    op.create_table(
        "grievance_cases",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "worker_id",
            sa.String(length=36),
            sa.ForeignKey("worker_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("case_code", sa.String(length=32), nullable=False, unique=True),
        sa.Column("category", sa.String(length=64), nullable=False, server_default="payment_discrepancy"),
        sa.Column("platform", sa.String(length=64), nullable=False, server_default="other"),
        sa.Column("jurisdiction_state", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("summary_title", sa.String(length=200), nullable=True),
        sa.Column("draft_details", sa.JSON(), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=True),
        sa.Column("confirmed_details", sa.JSON(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_confirmation_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_grievance_cases_worker_id", "grievance_cases", ["worker_id"])

    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("grievance_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_category", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("worker_note", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_evidence_items_case_id", "evidence_items", ["case_id"])

    op.create_table(
        "case_timeline_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("grievance_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("external_reference_id", sa.String(length=128), nullable=True),
        sa.Column("routing_channel_id", sa.String(length=64), nullable=True),
        sa.Column("snapshot_payload", sa.JSON(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_case_timeline_events_case_id", "case_timeline_events", ["case_id"])

    op.create_table(
        "routing_channels",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("platform", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("jurisdiction", sa.String(length=64), nullable=False, server_default="national"),
        sa.Column("tier", sa.String(length=64), nullable=False),
        sa.Column("channel_name", sa.String(length=160), nullable=False),
        sa.Column("contact_type", sa.String(length=64), nullable=False),
        sa.Column("contact_value", sa.String(length=255), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("expected_sla_days", sa.Integer(), nullable=True),
        sa.Column("verification_status", sa.String(length=32), nullable=False, server_default="verified"),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("last_verified_at", sa.String(length=32), nullable=False),
        sa.Column("disclaimer_text", sa.Text(), nullable=False),
    )
    op.create_index("ix_routing_channels_platform", "routing_channels", ["platform"])
    op.create_index("ix_routing_channels_category", "routing_channels", ["category"])


def downgrade() -> None:
    """Drop Phase 2 tables."""
    op.drop_index("ix_routing_channels_category", table_name="routing_channels")
    op.drop_index("ix_routing_channels_platform", table_name="routing_channels")
    op.drop_table("routing_channels")

    op.drop_index("ix_case_timeline_events_case_id", table_name="case_timeline_events")
    op.drop_table("case_timeline_events")

    op.drop_index("ix_evidence_items_case_id", table_name="evidence_items")
    op.drop_table("evidence_items")

    op.drop_index("ix_grievance_cases_worker_id", table_name="grievance_cases")
    op.drop_table("grievance_cases")
