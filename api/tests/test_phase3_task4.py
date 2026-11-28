"""Phase 3 Task 4 tests: mocked Gemini extraction, quote provenance, conflicts, and worker confirmation."""

import json
import subprocess

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.adapters.base import LLMProviderError
from app.adapters.gemini_llm import GEMINI_API_BASE, GeminiLLMProvider
from app.api.voice import extract_recording_draft_endpoint
from app.cases import service
from app.core.capabilities import build_capabilities_payload
from app.core.config import Settings
from app.core.database import Base, ensure_default_worker
from app.main import app
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import ConfirmCaseRequest, CreateCaseRequest, PaymentDiscrepancyDetails, UpdateDraftRequest
from app.schemas.voice import ExtractDraftRequest
from app.storage.local_store import LocalStorageService
from app.voice.extraction import (
    TranscriptNotApprovedError,
    extract_draft_from_recording,
    serialize_extraction_draft,
)
from app.voice.storage import store_case_recording
from app.voice.transcription import ConsentRequiredError, save_worker_transcript_edit


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


def _make_wav_bytes(tmp_path, name: str = "t4.wav") -> bytes:
    target = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", str(target)],
        check=True,
    )
    return target.read_bytes()


def _gemini_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        asr_provider="none",
        sarvam_api_key="",
        llm_provider="gemini",
        gemini_api_key="AIzaSyFakeGeminiTestKey999",
        gemini_model="gemini-3.8-flash",
    )


def _gemini_ok_envelope(extracted_obj: dict) -> dict:
    return {
        "candidates": [
            {
                "content": {
                    "parts": [{"text": json.dumps(extracted_obj)}],
                    "role": "model",
                },
                "finishReason": "STOP",
            }
        ]
    }


def _blank_llm_payload() -> dict:
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


def test_successful_gemini_extraction_provenance_and_draft_state(tmp_path):
    """Gemini extracts grounded fields with recording+layer provenance and keeps case in draft."""
    transcript = (
        "My Swiggy weekly payout for last week is missing 850 rupees. "
        "I completed 22 rain orders. I expected 2000 rupees and received 1150 rupees. "
        "I contacted support twice on ticket SW-441, and got no reply."
    )
    llm_json = _blank_llm_payload()
    llm_json.update(
        {
            "platform": {"value": "swiggy", "source_quote": "Swiggy"},
            "grievance_category": {"value": "payment_discrepancy", "source_quote": "weekly payout for last week is missing 850 rupees"},
            "short_issue_summary": {"value": "Missing Rs 850 on Swiggy weekly payout", "source_quote": "weekly payout for last week is missing 850 rupees"},
            "disputed_amount": {"value": 850, "source_quote": "missing 850 rupees"},
            "currency": {"value": "rupees", "source_quote": "850 rupees"},
            "relevant_period": {"value": "last week", "source_quote": "last week"},
            "work_completed": {"value": "completed 22 rain orders", "source_quote": "completed 22 rain orders"},
            "expected_payment_amount": {"value": 2000, "source_quote": "expected 2000 rupees"},
            "actual_payment_amount": {"value": 1150, "source_quote": "received 1150 rupees"},
            "prior_ticket_id": {"value": "SW-441", "source_quote": "ticket SW-441"},
            "previous_support_contacts": {"value": "contacted support twice", "source_quote": "contacted support twice"},
            "support_responses": {"value": "got no reply", "source_quote": "got no reply"},
            "missing_information": ["Exact payout cycle dates"],
            "suggested_followup_questions": ["What exact start and end dates covered last week's payout?"],
        }
    )

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_gemini_ok_envelope(llm_json))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_ok.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="c.wav")
        # Simulate pre-existing immutable raw transcript AND separate worker-edited transcript
        rec.raw_transcript = "Original raw ASR text unchanged."
        db.commit()
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

        assert len(captured) == 1
        req = captured[0]
        assert str(req.url) == f"{GEMINI_API_BASE}/gemini-3.8-flash:generateContent"
        assert req.headers["x-goog-api-key"] == "AIzaSyFakeGeminiTestKey999"
        req_body = json.loads(req.read())
        assert req_body["generationConfig"]["responseMimeType"] == "application/json"
        assert "responseJsonSchema" in req_body["generationConfig"]

        assert draft.extraction_status == "completed"
        assert draft.llm_provider == "gemini"
        assert draft.llm_model == "gemini-3.8-flash"
        assert draft.source_transcript_layer == "worker_edited_transcript"
        assert draft.stripped_ungrounded_fields is None
        assert draft.extracted_fields["discrepancy_amount_inr"] == 850
        assert draft.extracted_fields["currency"] == "rupees"
        assert draft.extracted_fields["expected_amount_inr"] == 2000
        assert draft.extracted_fields["received_amount_inr"] == 1150
        assert draft.extracted_fields["work_completed_summary"] == "completed 22 rain orders"
        assert draft.extracted_fields["support_contact_summary"] == "contacted support twice"
        assert draft.extracted_fields["support_response_summary"] == "got no reply"
        # Unknown fields must stay null
        assert draft.extracted_fields["work_city"] is None
        assert draft.extracted_fields["incentive_scheme_name"] is None

        prov = draft.field_provenance["discrepancy_amount_inr"]
        assert prov["voice_recording_id"] == rec.id
        assert prov["transcript_layer"] == "worker_edited_transcript"
        assert prov["source_quote"] == "missing 850 rupees"

        # Case MUST stay in draft, original ASR transcript preserved, and key never leaked
        db.refresh(case)
        db.refresh(rec)
        assert case.status == "draft"
        assert case.confirmed_details is None
        assert case.platform == "swiggy"
        assert rec.raw_transcript == "Original raw ASR text unchanged."
        assert rec.worker_edited_transcript == transcript
        serialized = service.serialize_case(db, case)
        assert len(serialized["extraction_drafts"]) == 1
        assert "AIzaSyFakeGeminiTestKey999" not in json.dumps(serialized)


def test_unsupported_quotations_and_schema_violations_rejected(tmp_path):
    """Fabricated/paraphrased quotes and invalid schema values are stripped to null."""
    approved_text = "My Uber daily bonus is missing."
    llm_json = _blank_llm_payload()
    llm_json.update(
        {
            "platform": {"value": "uber", "source_quote": "Uber"},
            # Fabricated/paraphrased quote not in approved_text
            "expected_payment_amount": {"value": 900, "source_quote": "I was promised 900 rupees"},
            # Negative number violates >= 0 schema even with matching quote
            "disputed_amount": {"value": -50, "source_quote": "daily bonus is missing"},
            # Missing quote
            "work_city": {"value": "Delhi", "source_quote": None},
        }
    )

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(
        settings,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_gemini_ok_envelope(llm_json))),
    )
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_strip.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="uber"))
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="u.wav")
        rec.raw_transcript = "Yesterday I drove in Delhi."
        db.commit()
        save_worker_transcript_edit(db, case=case, recording=rec, edited_transcript=approved_text)

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

        assert draft.extracted_fields["platform"] == "uber"
        assert draft.extracted_fields["expected_amount_inr"] is None
        assert draft.extracted_fields["discrepancy_amount_inr"] is None
        assert draft.extracted_fields["work_city"] is None
        assert "expected_amount_inr" in draft.stripped_ungrounded_fields
        assert "discrepancy_amount_inr" in draft.stripped_ungrounded_fields
        assert "work_city" in draft.stripped_ungrounded_fields
        assert case.draft_details["expected_amount_inr"] is None


def test_multiple_recordings_preserve_provenance_and_flag_conflicts(tmp_path):
    """Second recording fills null fields without silently overwriting conflicting facts."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        payload = _blank_llm_payload()
        if calls == 1:
            payload.update(
                {
                    "platform": {"value": "swiggy", "source_quote": "Swiggy"},
                    "expected_payment_amount": {"value": 1200, "source_quote": "expected 1200"},
                    "short_issue_summary": {"value": "Short payout", "source_quote": "expected 1200"},
                }
            )
        else:
            payload.update(
                {
                    # Contradicts recording #1's 1200
                    "expected_payment_amount": {"value": 1500, "source_quote": "actually expected 1500"},
                    # Fills previously null field (exact substring match)
                    "work_city": {"value": "Pune", "source_quote": "In Pune"},
                }
            )
        return httpx.Response(200, json=_gemini_ok_envelope(payload))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_multi.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="swiggy"))
        rec1 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="c1.wav")
        rec2 = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="c2.wav")
        save_worker_transcript_edit(db, case=case, recording=rec1, edited_transcript="Swiggy expected 1200.")
        save_worker_transcript_edit(db, case=case, recording=rec2, edited_transcript="In Pune actually expected 1500.")

        d1 = extract_draft_from_recording(
            db,
            case=case,
            recording=rec1,
            settings=settings,
            consent_granted=True,
            transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript",
            llm_provider=provider,
        )
        d2 = extract_draft_from_recording(
            db,
            case=case,
            recording=rec2,
            settings=settings,
            consent_granted=True,
            transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript",
            llm_provider=provider,
        )

        db.refresh(case)
        # First value NOT silently overwritten; new null field populated with rec2 provenance
        assert case.draft_details["expected_amount_inr"] == 1200
        assert case.draft_details["work_city"] == "Pune"
        assert case.field_provenance["expected_amount_inr"]["voice_recording_id"] == rec1.id
        assert case.field_provenance["work_city"]["voice_recording_id"] == rec2.id

        # Conflict explicitly surfaced on draft 2 with both provenances
        assert d1.conflict_suggestions is None
        assert "expected_amount_inr" in d2.conflict_suggestions
        conflict = d2.conflict_suggestions["expected_amount_inr"]
        assert conflict["existing_value"] == 1200
        assert conflict["proposed_value"] == 1500
        assert conflict["proposed_provenance"]["voice_recording_id"] == rec2.id

        # Worker reviews, resolves/edits draft, and explicitly confirms inside wallet
        service.update_draft_case(
            db,
            case=case,
            payload=UpdateDraftRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(
                    platform="swiggy",
                    expected_amount_inr=1500,
                    work_city="Pune",
                    issue_description="Short payout verified by worker",
                ),
            ),
        )
        confirmed = service.confirm_case(
            db,
            case=case,
            payload=ConfirmCaseRequest(
                confirm=True,
                platform="swiggy",
                details=PaymentDiscrepancyDetails(
                    platform="swiggy",
                    expected_amount_inr=1500,
                    work_city="Pune",
                    issue_description="Short payout verified by worker",
                ),
            ),
        )
        assert confirmed.status == "confirmed"
        assert confirmed.confirmed_details["expected_amount_inr"] == 1500


def test_consent_and_worker_transcript_approval_enforced(tmp_path):
    """Extraction refuses to run if consent or worker transcript approval is missing."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=_gemini_ok_envelope(_blank_llm_payload()))

    settings = _gemini_settings(tmp_path)
    provider = GeminiLLMProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_gates.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="rapido"))
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r.wav")
        rec.raw_transcript = "Rapido bonus missing"
        db.commit()

        with pytest.raises(ConsentRequiredError, match="consent"):
            extract_draft_from_recording(
                db,
                case=case,
                recording=rec,
                settings=settings,
                consent_granted=False,
                transcript_approved_by_worker=True,
                transcript_layer="raw_transcript",
                llm_provider=provider,
            )

        with pytest.raises(TranscriptNotApprovedError, match="approve"):
            extract_draft_from_recording(
                db,
                case=case,
                recording=rec,
                settings=settings,
                consent_granted=True,
                transcript_approved_by_worker=False,
                transcript_layer="raw_transcript",
                llm_provider=provider,
            )
        assert calls == 0


def test_missing_key_and_gemini_error_and_invalid_json_handling(tmp_path):
    """Missing key records key_not_set; provider errors/timeouts/bad JSON fail safely without leaking keys."""
    keyless_settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        llm_provider="none",
        gemini_api_key="",
    )
    caps = build_capabilities_payload(keyless_settings)
    assert caps["llm"]["status_code"] == "key_not_set"

    storage = LocalStorageService(keyless_settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_err.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="swiggy"))
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="e.wav")
        save_worker_transcript_edit(db, case=case, recording=rec, edited_transcript="Swiggy payout missing.")

        keyless_draft = extract_draft_from_recording(
            db,
            case=case,
            recording=rec,
            settings=keyless_settings,
            consent_granted=True,
            transcript_approved_by_worker=True,
            transcript_layer="worker_edited_transcript",
        )
        assert keyless_draft.extraction_status == "key_not_set"
        assert keyless_draft.llm_provider == "none"
        assert keyless_draft.extracted_fields is None
        assert len(keyless_draft.missing_fields) > 0

        # Test provider errors, timeouts, and invalid JSON with configured key
        configured = _gemini_settings(tmp_path)
        error_handlers = [
            (lambda req: httpx.Response(403, json={"error": {"message": "Bad key AIzaSyFakeGeminiTestKey999"}}), "provider_auth_error"),
            (lambda req: httpx.Response(429, json={"error": {"message": "Quota exceeded"}}), "rate_limited"),
            (lambda req: httpx.Response(500, json={"error": {"message": "Server error"}}), "provider_server_error"),
            (lambda req: httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "not json {"}]}}]}), "invalid_json_output"),
            (lambda req: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=req)), "provider_timeout"),
        ]

        for handler, expected_code in error_handlers:
            provider = GeminiLLMProvider(configured, transport=httpx.MockTransport(handler))
            with pytest.raises(LLMProviderError) as exc_info:
                extract_draft_from_recording(
                    db,
                    case=case,
                    recording=rec,
                    settings=configured,
                    consent_granted=True,
                    transcript_approved_by_worker=True,
                    transcript_layer="worker_edited_transcript",
                    llm_provider=provider,
                )
            assert exc_info.value.error_code == expected_code
            assert "AIzaSyFakeGeminiTestKey999" not in exc_info.value.user_message


def test_extract_endpoint_openapi_and_cross_case_isolation(tmp_path):
    """Extract endpoint registers cleanly, enforces gates, and blocks cross-case access."""
    openapi_paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/cases/{case_id}/voice/{recording_id}/extract" in openapi_paths

    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        llm_provider="none",
        gemini_api_key="",
    )
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t4_ep.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case_a = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="swiggy"))
        case_b = service.create_payment_case(db, worker_id=worker_id, payload=CreateCaseRequest(platform="uber"))
        rec_a = store_case_recording(db, case=case_a, storage=storage, raw_bytes=wav_bytes, original_filename="a.wav")
        save_worker_transcript_edit(db, case=case_a, recording=rec_a, edited_transcript="Swiggy bonus missing")

        with pytest.raises(HTTPException) as exc_404:
            extract_recording_draft_endpoint(
                case_id=case_b.id,
                recording_id=rec_a.id,
                payload=ExtractDraftRequest(
                    consent_granted=True,
                    transcript_approved_by_worker=True,
                    transcript_layer="worker_edited_transcript",
                ),
                db=db,
                worker_id=worker_id,
                settings=settings,
            )
        assert exc_404.value.status_code == 404

        res = extract_recording_draft_endpoint(
            case_id=case_a.id,
            recording_id=rec_a.id,
            payload=ExtractDraftRequest(
                consent_granted=True,
                transcript_approved_by_worker=True,
                transcript_layer="worker_edited_transcript",
            ),
            db=db,
            worker_id=worker_id,
            settings=settings,
        )
        assert res["draft"]["extraction_status"] == "key_not_set"
        assert len(res["case"]["extraction_drafts"]) == 1
        assert serialize_extraction_draft(db.get(CaseExtractionDraft, res["draft"]["id"]))["id"] == res["draft"]["id"]
