"""Phase 3 Task 1 tests: VoiceRecording and CaseExtractionDraft ORM relationships and FK semantics."""

import uuid

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.cases import service
from app.core.database import Base, ensure_default_worker
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import CreateCaseRequest, PaymentDiscrepancyDetails


def _make_fk_session_factory(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fks(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_voice_recording_and_draft_relationships_and_deletion_rules(tmp_path):
    """Deleting a recording preserves historical drafts via SET NULL; deleting a case cascades."""
    SessionFactory = _make_fk_session_factory(tmp_path / "phase3_task1.db")

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(
                    platform="swiggy",
                    issue_description="Incentive shortfall test",
                ),
            ),
        )

        rec1 = VoiceRecording(
            id=str(uuid.uuid4()),
            case_id=case.id,
            recording_order=1,
            storage_key=f"{case.id}/audio/rec1.webm",
            original_filename="clip1.webm",
            mime_type="audio/webm",
            codec_name="opus",
            size_bytes=12345,
            sha256_hash="a" * 64,
            measured_duration_sec=12.4,
            asr_provider="none",
            transcription_status="key_not_set",
            raw_transcript=None,
            worker_edited_transcript="Swiggy weekend incentive short by 850 rupees",
        )
        rec2 = VoiceRecording(
            id=str(uuid.uuid4()),
            case_id=case.id,
            recording_order=2,
            storage_key=f"{case.id}/audio/rec2.webm",
            original_filename="clip2.webm",
            mime_type="audio/webm",
            codec_name="opus",
            size_bytes=9876,
            sha256_hash="b" * 64,
            measured_duration_sec=8.2,
            asr_provider="sarvam",
            asr_model="saaras:v4",
            transcription_status="completed",
            raw_transcript="Expected 1200 received 350",
        )
        db.add_all([rec1, rec2])
        db.flush()

        draft1 = CaseExtractionDraft(
            id=str(uuid.uuid4()),
            case_id=case.id,
            voice_recording_id=rec1.id,
            source_transcript_layer="worker_edited_transcript",
            source_transcript_snapshot="Swiggy weekend incentive short by 850 rupees",
            llm_provider="none",
            extraction_status="key_not_set",
        )
        db.add(draft1)
        db.commit()

        db.refresh(case)
        assert [r.recording_order for r in case.voice_recordings] == [1, 2]
        assert len(case.extraction_drafts) == 1

        # Deleting rec1 must NOT delete draft1 (voice_recording_id -> NULL) and must keep rec2
        db.delete(rec1)
        db.commit()

        reloaded_draft = db.scalar(select(CaseExtractionDraft).where(CaseExtractionDraft.id == draft1.id))
        assert reloaded_draft is not None
        assert reloaded_draft.voice_recording_id is None
        assert reloaded_draft.source_transcript_snapshot == "Swiggy weekend incentive short by 850 rupees"
        remaining_recs = list(db.scalars(select(VoiceRecording).where(VoiceRecording.case_id == case.id)))
        assert len(remaining_recs) == 1
        assert remaining_recs[0].id == rec2.id

        # Deleting the parent case cascades to both voice_recordings and case_extraction_drafts
        db.delete(case)
        db.commit()
        assert db.scalar(select(VoiceRecording).where(VoiceRecording.id == rec2.id)) is None
        assert db.scalar(select(CaseExtractionDraft).where(CaseExtractionDraft.id == draft1.id)) is None
