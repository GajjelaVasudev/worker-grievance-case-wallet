"""Phase 3 Task 3 tests: mocked Sarvam saaras:v4 ASR, consent, transcript preservation, and error handling."""

import json
import subprocess

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.adapters.base import ASRProviderError
from app.adapters.sarvam_asr import SARVAM_STT_ENDPOINT, SarvamASRProvider
from app.api.voice import (
    edit_recording_transcript,
    transcribe_recording_endpoint,
)
from app.cases import service
from app.core.capabilities import build_capabilities_payload
from app.core.config import Settings
from app.core.database import Base, ensure_default_worker
from app.main import app
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed
from app.schemas.case import CreateCaseRequest, PaymentDiscrepancyDetails
from app.schemas.voice import TranscribeRecordingRequest, UpdateTranscriptRequest
from app.storage.local_store import LocalStorageService
from app.voice.storage import serialize_voice_recording, store_case_recording
from app.voice.transcription import (
    ConsentRequiredError,
    DuplicateTranscriptionError,
    save_worker_transcript_edit,
    transcribe_case_recording,
)


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


def _make_wav_bytes(tmp_path, name: str = "sample.wav") -> bytes:
    target = tmp_path / name
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.5",
            str(target),
        ],
        check=True,
    )
    return target.read_bytes()


def _configured_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        asr_provider="sarvam",
        sarvam_api_key="sk_sarvam_test_secret_12345",
        sarvam_stt_model="saaras:v4",
        sarvam_stt_mode="codemix",
        llm_provider="none",
        gemini_api_key="",
    )


def test_sarvam_request_construction_and_successful_transcription(tmp_path):
    """Verify exact Sarvam endpoint, headers, multipart fields, response parsing, and timeline logging."""
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "request_id": "req_sarvam_001",
                "transcript": "Swiggy weekend incentive 850 rupees kam aaya hai",
                "language_code": "hi-IN",
                "language_probability": 0.94,
            },
        )

    settings = _configured_settings(tmp_path)
    provider = SarvamASRProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t3_ok.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(platform="swiggy", issue_description="Incentive short"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="voice1.wav")

        updated = transcribe_case_recording(
            db,
            case=case,
            recording=rec,
            storage=storage,
            settings=settings,
            consent_granted=True,
            language_code="hi-IN",
            asr_provider=provider,
        )

        assert len(captured) == 1
        req = captured[0]
        assert str(req.url) == SARVAM_STT_ENDPOINT
        assert req.method == "POST"
        assert req.headers["api-subscription-key"] == "sk_sarvam_test_secret_12345"
        body_bytes = req.read()
        assert b'name="model"\r\n\r\nsaaras:v4' in body_bytes
        assert b'name="mode"\r\n\r\ncodemix' in body_bytes
        assert b'name="language_code"\r\n\r\nhi-IN' in body_bytes
        assert b'name="file"; filename="voice1.wav"' in body_bytes
        assert b"Content-Type: audio/wav" in body_bytes

        assert updated.transcription_status == "completed"
        assert updated.asr_provider == "sarvam"
        assert updated.asr_model == "saaras:v4"
        assert updated.asr_request_id == "req_sarvam_001"
        assert updated.detected_language == "hi-IN"
        assert updated.language_probability == pytest.approx(0.94)
        assert updated.raw_transcript == "Swiggy weekend incentive 850 rupees kam aaya hai"
        assert updated.worker_edited_transcript is None
        assert updated.sarvam_consent_granted is True
        assert updated.sarvam_consented_at is not None

        serialized = serialize_voice_recording(updated)
        assert serialized["transcript_origin"] == "sarvam_asr"
        assert serialized["effective_transcript"] == "Swiggy weekend incentive 850 rupees kam aaya hai"
        assert "sk_sarvam_test_secret_12345" not in json.dumps(serialized)

        db.refresh(case)
        assert [e.event_type for e in case.timeline_events] == ["case_created", "voice_recorded", "asr_transcribed"]


def test_consent_required_before_sarvam_call(tmp_path):
    """Refusing consent must block Sarvam HTTP calls and keep consent false."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"transcript": "should not run"})

    settings = _configured_settings(tmp_path)
    provider = SarvamASRProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t3_consent.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="uber",
                details=PaymentDiscrepancyDetails(platform="uber", issue_description="Quest missing"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="u.wav")

        with pytest.raises(ConsentRequiredError, match="consent"):
            transcribe_case_recording(
                db,
                case=case,
                recording=rec,
                storage=storage,
                settings=settings,
                consent_granted=False,
                asr_provider=provider,
            )

        assert calls == 0
        assert rec.sarvam_consent_granted is False
        assert rec.raw_transcript is None


def test_keyless_mode_and_manual_transcript_distinction(tmp_path):
    """Without Sarvam key, capabilities report key_not_set and manual transcripts never pose as ASR."""
    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        asr_provider="none",
        sarvam_api_key="",
        llm_provider="none",
        gemini_api_key="",
    )
    caps = build_capabilities_payload(settings)
    assert caps["asr"]["configured"] is False
    assert caps["asr"]["status_code"] == "key_not_set"

    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t3_keyless.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="rapido",
                details=PaymentDiscrepancyDetails(platform="rapido", issue_description="Bonus issue"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="r.wav")

        after_keyless = transcribe_case_recording(
            db,
            case=case,
            recording=rec,
            storage=storage,
            settings=settings,
            consent_granted=True,
        )
        assert after_keyless.transcription_status == "key_not_set"
        assert after_keyless.raw_transcript is None
        assert after_keyless.asr_provider == "none"

        manual = save_worker_transcript_edit(
            db,
            case=case,
            recording=after_keyless,
            edited_transcript="Worker typed manual summary: expected Rs 850 bonus.",
        )
        assert manual.raw_transcript is None
        assert manual.worker_edited_transcript == "Worker typed manual summary: expected Rs 850 bonus."
        serialized = serialize_voice_recording(manual)
        assert serialized["transcript_origin"] == "manual_worker_entry"


@pytest.mark.parametrize(
    ("status_code", "body", "expected_error_code"),
    [
        (401, {"error": {"code": "invalid_api_key_error", "message": "Bad key sk_sarvam_test_secret_12345"}}, "provider_auth_error"),
        (402, {"error": {"code": "insufficient_quota_error", "message": "No credits available"}}, "insufficient_credits"),
        (413, {"error": {"code": "payload_too_large", "message": "Too large"}}, "payload_too_large"),
        (429, {"error": {"code": "rate_limit_exceeded_error", "message": "Slow down"}}, "rate_limited"),
        (500, {"error": {"code": "internal_server_error", "message": "Upstream error"}}, "provider_server_error"),
        (200, {"transcript": "   "}, "empty_transcript"),
    ],
)
def test_sarvam_http_and_empty_error_mapping_redacts_secrets(tmp_path, status_code, body, expected_error_code):
    """Provider errors persist failed status safely without leaking the API key or faking success."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status_code, json=body)

    settings = _configured_settings(tmp_path)
    provider = SarvamASRProvider(settings, transport=httpx.MockTransport(handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / f"err_{status_code}_{expected_error_code}.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(platform="swiggy", issue_description="Error mapping test"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="err.wav")

        with pytest.raises(ASRProviderError) as exc_info:
            transcribe_case_recording(
                db,
                case=case,
                recording=rec,
                storage=storage,
                settings=settings,
                consent_granted=True,
                asr_provider=provider,
            )

        assert calls == 1  # Never silently retry
        assert exc_info.value.error_code == expected_error_code
        assert "sk_sarvam_test_secret_12345" not in exc_info.value.user_message
        assert rec.transcription_status == "failed"
        assert rec.error_code == expected_error_code
        assert rec.raw_transcript is None
        assert "sk_sarvam_test_secret_12345" not in (rec.error_message or "")


def test_sarvam_timeout_network_and_malformed_json_errors(tmp_path):
    """Timeout, network failures, and non-JSON responses are mapped cleanly without silent retries."""
    settings = _configured_settings(tmp_path)
    wav_bytes = _make_wav_bytes(tmp_path)

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Timed out", request=request)

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("DNS failed", request=request)

    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>502 bad gateway</html>", headers={"Content-Type": "text/html"})

    for handler, expected_code in (
        (timeout_handler, "provider_timeout"),
        (network_handler, "network_error"),
        (malformed_handler, "malformed_response"),
    ):
        provider = SarvamASRProvider(settings, transport=httpx.MockTransport(handler))
        with pytest.raises(ASRProviderError) as exc_info:
            provider.transcribe_audio(audio_bytes=wav_bytes, filename="x.wav", mime_type="audio/wav")
        assert exc_info.value.error_code == expected_code


def test_original_transcript_preserved_and_retry_rules(tmp_path):
    """Worker edits stay separate; completed transcription blocks duplicate calls; failed transcription allows explicit retry."""
    attempts = 0

    def flaky_then_ok_handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded_error", "message": "Try later"}})
        return httpx.Response(
            200,
            json={
                "request_id": "req_retry_ok",
                "transcript": "Expected Rs 850 but got Rs 300.",
                "language_code": "en-IN",
                "language_probability": 0.99,
            },
        )

    settings = _configured_settings(tmp_path)
    provider = SarvamASRProvider(settings, transport=httpx.MockTransport(flaky_then_ok_handler))
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t3_preserve.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="uber",
                details=PaymentDiscrepancyDetails(platform="uber", issue_description="Retry test"),
            ),
        )
        rec = store_case_recording(db, case=case, storage=storage, raw_bytes=wav_bytes, original_filename="retry.wav")

        # First attempt fails (429) -> persists failed status, makes 1 request
        with pytest.raises(ASRProviderError):
            transcribe_case_recording(
                db,
                case=case,
                recording=rec,
                storage=storage,
                settings=settings,
                consent_granted=True,
                asr_provider=provider,
            )
        assert attempts == 1
        assert rec.transcription_status == "failed"

        # Explicit retry succeeds -> sets raw_transcript
        completed = transcribe_case_recording(
            db,
            case=case,
            recording=rec,
            storage=storage,
            settings=settings,
            consent_granted=True,
            asr_provider=provider,
        )
        assert attempts == 2
        assert completed.transcription_status == "completed"
        assert completed.raw_transcript == "Expected Rs 850 but got Rs 300."

        # Duplicate transcription call on completed recording is blocked (0 extra HTTP calls)
        with pytest.raises(DuplicateTranscriptionError, match="cannot be overwritten"):
            transcribe_case_recording(
                db,
                case=case,
                recording=completed,
                storage=storage,
                settings=settings,
                consent_granted=True,
                asr_provider=provider,
            )
        assert attempts == 2

        # Worker edits transcript separately -> raw_transcript stays identical
        edited = save_worker_transcript_edit(
            db,
            case=case,
            recording=completed,
            edited_transcript="Expected Rs 850 for Sunday quest, received Rs 300 only.",
        )
        assert edited.raw_transcript == "Expected Rs 850 but got Rs 300."
        assert edited.worker_edited_transcript == "Expected Rs 850 for Sunday quest, received Rs 300 only."
        serialized = serialize_voice_recording(edited)
        assert serialized["transcript_origin"] == "sarvam_asr_with_worker_edits"
        assert serialized["raw_transcript"] == "Expected Rs 850 but got Rs 300."


def test_voice_api_routes_registered_and_case_scoped(tmp_path):
    """Voice endpoints register in OpenAPI and reject cross-case access and unconsented calls."""
    openapi_paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/cases/{case_id}/voice" in openapi_paths
    assert "/api/cases/{case_id}/voice/{recording_id}/audio" in openapi_paths
    assert "/api/cases/{case_id}/voice/{recording_id}/transcribe" in openapi_paths
    assert "/api/cases/{case_id}/voice/{recording_id}/transcript" in openapi_paths
    assert "/api/cases/{case_id}/voice/{recording_id}" in openapi_paths

    settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "store"),
        asr_provider="none",
        sarvam_api_key="",
    )
    storage = LocalStorageService(settings.evidence_storage_dir, 10, 30.0)
    SessionFactory = _make_fk_session_factory(tmp_path / "t3_routes.db")
    wav_bytes = _make_wav_bytes(tmp_path)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case_a = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                details=PaymentDiscrepancyDetails(platform="swiggy", issue_description="Case A"),
            ),
        )
        case_b = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="rapido",
                details=PaymentDiscrepancyDetails(platform="rapido", issue_description="Case B"),
            ),
        )
        rec_a = store_case_recording(db, case=case_a, storage=storage, raw_bytes=wav_bytes, original_filename="a.wav")

        # Cross-case access rejected with 404
        with pytest.raises(HTTPException) as exc_404:
            transcribe_recording_endpoint(
                case_id=case_b.id,
                recording_id=rec_a.id,
                payload=TranscribeRecordingRequest(consent_granted=True),
                db=db,
                worker_id=worker_id,
                settings=settings,
                storage=storage,
            )
        assert exc_404.value.status_code == 404

        # Unconsented call rejected with 400
        with pytest.raises(HTTPException) as exc_400:
            transcribe_recording_endpoint(
                case_id=case_a.id,
                recording_id=rec_a.id,
                payload=TranscribeRecordingRequest(consent_granted=False),
                db=db,
                worker_id=worker_id,
                settings=settings,
                storage=storage,
            )
        assert exc_400.value.status_code == 400

        # Keyless transcription returns key_not_set gracefully; manual edit works via endpoint
        res = transcribe_recording_endpoint(
            case_id=case_a.id,
            recording_id=rec_a.id,
            payload=TranscribeRecordingRequest(consent_granted=True),
            db=db,
            worker_id=worker_id,
            settings=settings,
            storage=storage,
        )
        assert res["recording"]["transcription_status"] == "key_not_set"

        edited = edit_recording_transcript(
            case_id=case_a.id,
            recording_id=rec_a.id,
            payload=UpdateTranscriptRequest(worker_edited_transcript="Manual note for Case A"),
            db=db,
            worker_id=worker_id,
        )
        assert edited["recording"]["transcript_origin"] == "manual_worker_entry"
        assert edited["recording"]["raw_transcript"] is None
        assert len(edited["case"]["voice_recordings"]) == 1
