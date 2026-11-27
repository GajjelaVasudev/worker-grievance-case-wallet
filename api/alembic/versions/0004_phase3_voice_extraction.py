"""Phase 3 voice recordings and structured extraction draft tables.

Revision ID: 0004_phase3_voice_extraction
Revises: 0003_phase2_audit_updates
Create Date: 2026-11-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_phase3_voice_extraction"
down_revision: Union[str, None] = "0003_phase2_audit_updates"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create voice_recordings and case_extraction_drafts tables."""
    op.create_table(
        "voice_recordings",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("grievance_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("recording_order", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=128), nullable=False),
        sa.Column("codec_name", sa.String(length=64), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
        sa.Column("measured_duration_sec", sa.Float(), nullable=False),
        sa.Column("asr_provider", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("asr_model", sa.String(length=64), nullable=True),
        sa.Column("asr_request_id", sa.String(length=128), nullable=True),
        sa.Column("transcription_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("detected_language", sa.String(length=32), nullable=True),
        sa.Column("language_probability", sa.Float(), nullable=True),
        sa.Column("raw_transcript", sa.Text(), nullable=True),
        sa.Column("asr_transcribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worker_edited_transcript", sa.Text(), nullable=True),
        sa.Column("worker_edited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_demo_sample", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sarvam_consent_granted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sarvam_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_voice_recordings_case_id", "voice_recordings", ["case_id"])

    op.create_table(
        "case_extraction_drafts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=36),
            sa.ForeignKey("grievance_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "voice_recording_id",
            sa.String(length=36),
            sa.ForeignKey("voice_recordings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_transcript_layer", sa.String(length=32), nullable=False),
        sa.Column("source_transcript_snapshot", sa.Text(), nullable=False),
        sa.Column("llm_provider", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column("llm_model", sa.String(length=64), nullable=True),
        sa.Column("extraction_status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("gemini_consent_granted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("gemini_consented_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("extracted_fields", sa.JSON(), nullable=True),
        sa.Column("field_provenance", sa.JSON(), nullable=True),
        sa.Column("stripped_ungrounded_fields", sa.JSON(), nullable=True),
        sa.Column("conflict_suggestions", sa.JSON(), nullable=True),
        sa.Column("missing_fields", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_case_extraction_drafts_case_id", "case_extraction_drafts", ["case_id"])
    op.create_index(
        "ix_case_extraction_drafts_voice_recording_id",
        "case_extraction_drafts",
        ["voice_recording_id"],
    )


def downgrade() -> None:
    """Drop voice_recordings and case_extraction_drafts tables."""
    op.drop_index("ix_case_extraction_drafts_voice_recording_id", table_name="case_extraction_drafts")
    op.drop_index("ix_case_extraction_drafts_case_id", table_name="case_extraction_drafts")
    op.drop_table("case_extraction_drafts")

    op.drop_index("ix_voice_recordings_case_id", table_name="voice_recordings")
    op.drop_table("voice_recordings")
