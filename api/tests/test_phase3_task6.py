"""Phase 3 Task 6 tests: default platform conflict fix, genuine conflicts, confirmed safety, and Gemini default."""

import json
import subprocess

import httpx
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.adapters.gemini_llm import GEMINI_API_BASE, GeminiLLMProvider
from app.cases import service
from app.core.config import Settings
from app.core.database import Base, ensure_default_worker
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import ConfirmCaseRequest, CreateCaseRequest, PaymentDiscrepancyDetails
from app.storage.local_store import LocalStorageService
from app.voice.extraction import extract_draft_from_recording
from app.voice.storage import store_case_recording
from app.voice.transcription import save_worker_transcript_edit


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


def _make_wav_bytes(tmp_path, name: str = "t6.wav") -> bytes:
    target = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.0", str(target)],
        check=True,
    )
    return target.read_bytes()


def _gemini_settings(tmp_path, *, model: str | None = None) -> Settings:
    kwargs = {
        "_env_file": None,
        "database_url": "sqlite://",
        "evidence_storage_dir": str(tmp_path / "store"),
        "asr_provider": "none",
        "sarvam_api_key": "",
        "llm_provider": "gemini",
        "gemini_api_key": "AIzaSyTask6TestKeyOnly",
    }
    if model is not None:
        kwargs["gemini_model"] = model
    return Settings(**kwargs)


def _blank_payload() -> dict:
    null_entry = {"value": None, "source_quote": None}
    return {
        "platform": dict(null_entry),
        "grievance_category": dict(null_entry),
        "short_issue_summary": dict(null_entry),
        "disputed_amount": dict(null_entry),
        "currency": dict(null_entry),
        "relevant_period": dict(null_entry),
        "work_completed": dict(null_entry),
        "expected_payment_amount": dict(null_entry),
        "actual_payment_amount": dict(null_entry),
        "incentive_scheme_name": dict(null_entry),
        "work_city": dict(null_entry),
        "prior_ticket_id": dict(null_entry),
        "previous_support_contacts": dict(null_entry),
        "support_responses": dict(null_entry),
        "missing_information": [],
        "suggested_followup_questions": [],
    }


def _ok_envelope(payload: dict) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(payload)}], "role": "model"},
                "finishReason": "STOP",
            }
        ]
    }


def test_default_platform_other_replaced_by_grounded_swiggy_without_false_conflict(tmp_path):
    """Initial unconfirmed platform='other' updates cleanly to Swiggy without creating a conflict."""
    payload = _blank_payload()
    payload.update(
        {
            "platform": {"value": "swiggy", "source_quote": "Swiggy"},
            "grievance_category": {"value": "payment_discrepancy", "source_quote": "weekly payment is 850 rupees less"},
            "short_issue_summary": {
                "value": "Swiggy weekly payment is 850 rupees less than expected",
                "source_quote": "delivery partner for Swiggy. My weekly payment is 850 rupees less than expected.",
            },
            "disputed_amount": {"value": 850, "source_quote": "850 rupees"},
            "currency": {"value": "rupees", "source_quote": "850 rupees"},
        }
    )

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_ok_envelope(payload))))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t6_default_other.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        # Simulates creating a blank draft case before speaking, even with platform='other' in details
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="other",
                details=PaymentDiscrepancyDetails(platform="other"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r1.wav")
        transcript = "My name is Ravi. I work as a delivery partner for Swiggy. My weekly payment is 850 rupees less than expected."
        save_worker_transcript_edit(db, case=case, recording=rec, edited_transcript=transcript)

        draft = extract_draft_from_recording(
            db,
            case=case,
            recording=rec,
            settings=settings,
            consent_granted=True,
            transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript",
            llm_provider=provider,
        )

        db.refresh(case)
        assert draft.conflict_suggestions is None
        assert case.platform == "swiggy"
        assert case.draft_details["platform"] == "swiggy"
        assert case.field_provenance["platform"]["source_quote"] == "Swiggy"
        assert case.field_provenance["platform"]["voice_recording_id"] == rec.id
        assert case.confirmed_details is None


def test_genuine_platform_disagreement_between_recordings_creates_conflict(tmp_path):
    """A genuine platform disagreement across recordings is flagged with source provenance."""
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _blank_payload()
        if calls == 1:
            payload["platform"] = {"value": "swiggy", "source_quote": "Swiggy"}
        else:
            payload["platform"] = {"value": "zomato", "source_quote": "Zomato"}
        return httpx.Response(200, json=_ok_envelope(payload))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t6_platform_conflict.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest())
        rec1 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r1.wav")
        rec2 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r2.wav")
        save_worker_transcript_edit(db, case=case, recording=rec1, edited_transcript="Swiggy payout short.")
        save_worker_transcript_edit(db, case=case, recording=rec2, edited_transcript="Actually Zomato order payout short.")

        d1 = extract_draft_from_recording(
            db, case=case, recording=rec1, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )
        d2 = extract_draft_from_recording(
            db, case=case, recording=rec2, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )

        db.refresh(case)
        assert d1.conflict_suggestions is None
        assert case.platform == "swiggy"
        assert case.draft_details["platform"] == "swiggy"
        assert "platform" in d2.conflict_suggestions
        conflict = d2.conflict_suggestions["platform"]
        assert conflict["existing_value"] == "swiggy"
        assert conflict["existing_provenance"]["voice_recording_id"] == rec1.id
        assert conflict["proposed_value"] == "zomato"
        assert conflict["proposed_provenance"]["voice_recording_id"] == rec2.id


def test_provenanced_platform_other_disagreement_creates_conflict(tmp_path):
    """When recording #1 explicitly grounds platform='other', recording #2 saying Swiggy still flags conflict."""
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _blank_payload()
        if calls == 1:
            payload["platform"] = {"value": "other", "source_quote": "another local delivery app"}
        else:
            payload["platform"] = {"value": "swiggy", "source_quote": "Swiggy"}
        return httpx.Response(200, json=_ok_envelope(payload))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t6_prov_other.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest())
        rec1 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r1.wav")
        rec2 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r2.wav")
        save_worker_transcript_edit(db, case=case, recording=rec1, edited_transcript="I deliver on another local delivery app.")
        save_worker_transcript_edit(db, case=case, recording=rec2, edited_transcript="Actually Swiggy payout short.")

        extract_draft_from_recording(
            db, case=case, recording=rec1, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )
        d2 = extract_draft_from_recording(
            db, case=case, recording=rec2, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )

        db.refresh(case)
        assert case.draft_details["platform"] == "other"
        assert "platform" in d2.conflict_suggestions
        assert d2.conflict_suggestions["platform"]["existing_value"] == "other"
        assert d2.conflict_suggestions["platform"]["proposed_value"] == "swiggy"


def test_amount_disagreement_850_vs_1200_flagged_and_confirmed_never_overwritten(tmp_path):
    """Genuine ₹850 vs ₹1,200 disagreement flags conflict while confirmed_details remain immutable."""
    calls = 0

    def handler(_req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _blank_payload()
        if calls == 1:
            payload.update(
                {
                    "platform": {"value": "swiggy", "source_quote": "Swiggy"},
                    "disputed_amount": {"value": 850, "source_quote": "850 rupees"},
                    "currency": {"value": "rupees", "source_quote": "850 rupees"},
                    "short_issue_summary": {"value": "Swiggy payout missing 850 rupees", "source_quote": "Swiggy payout missing 850 rupees"},
                }
            )
        else:
            payload.update(
                {
                    "platform": {"value": "zomato", "source_quote": "Zomato"},
                    "disputed_amount": {"value": 1200, "source_quote": "1200 rupees"},
                    "currency": {"value": "rupees", "source_quote": "1200 rupees"},
                }
            )
        return httpx.Response(200, json=_ok_envelope(payload))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t6_confirmed_safe.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="other"))
        rec1 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r1.wav")
        rec2 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r2.wav")
        save_worker_transcript_edit(db, case=case, recording=rec1, edited_transcript="Swiggy payout missing 850 rupees.")
        save_worker_transcript_edit(db, case=case, recording=rec2, edited_transcript="Zomato payout missing 1200 rupees.")

        extract_draft_from_recording(
            db, case=case, recording=rec1, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )
        db.refresh(case)
        confirmed = service.confirm_case(
            db,
            case=case,
            payload=ConfirmCaseRequest(
                confirm=True,
                platform="swiggy",
                details=PaymentDiscrepancyDetails.model_validate(case.draft_details),
                worker_confirmation_note="Confirmed 850 rupees Swiggy dispute",
            ),
        )
        confirmed_snapshot = dict(confirmed.confirmed_details)

        d2 = extract_draft_from_recording(
            db, case=case, recording=rec2, settings=settings,
            consent_granted=True, transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript", llm_provider=provider,
        )

        db.refresh(case)
        assert case.status == "confirmed"
        assert case.confirmed_details == confirmed_snapshot
        assert case.confirmed_details["platform"] == "swiggy"
        assert case.confirmed_details["discrepancy_amount_inr"] == 850.0
        assert case.draft_details["discrepancy_amount_inr"] == 850.0
        assert "discrepancy_amount_inr" in d2.conflict_suggestions
        assert d2.conflict_suggestions["discrepancy_amount_inr"]["existing_value"] == 850.0
        assert d2.conflict_suggestions["discrepancy_amount_inr"]["proposed_value"] == 1200.0
        assert "platform" in d2.conflict_suggestions


def test_gemini_adapter_uses_default_36_and_honors_env_override(tmp_path, monkeypatch):
    """Gemini adapter defaults to gemini-3.6-flash and respects GEMINI_MODEL env overrides."""
    captured_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured_urls.append(str(req.url))
        return httpx.Response(200, json=_ok_envelope(_blank_payload()))

    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    default_settings = _gemini_settings(tmp_path)
    GeminiLLMProvider(default_settings, transport=httpx.MockTransport(handler)).extract_payment_discrepancy(
        approved_transcript="Swiggy payout missing 850 rupees."
    )

    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    override_settings = _gemini_settings(tmp_path)
    GeminiLLMProvider(override_settings, transport=httpx.MockTransport(handler)).extract_payment_discrepancy(
        approved_transcript="Swiggy payout missing 850 rupees."
    )

    assert captured_urls == [
        f"{GEMINI_API_BASE}/gemini-3.6-flash:generateContent",
        f"{GEMINI_API_BASE}/gemini-3.5-flash-lite:generateContent",
    ]
