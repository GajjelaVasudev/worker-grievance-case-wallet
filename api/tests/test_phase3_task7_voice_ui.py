"""Phase 3 Task 7 tests: frontend-to-backend voice recording, playback, delete, Sarvam consent, edit, and error paths."""

import subprocess

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.sarvam_asr import SarvamASRProvider
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401


def _make_wav_bytes(tmp_path, name: str = "clip.wav", duration_sec: float = 1.2) -> bytes:
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
            f"sine=frequency=440:duration={duration_sec}",
            str(target),
        ],
        check=True,
    )
    return target.read_bytes()


def _make_webm_opus_bytes(tmp_path, name: str = "clip.webm", duration_sec: float = 1.2) -> bytes:
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
            f"sine=frequency=440:duration={duration_sec}",
            "-c:a",
            "libopus",
            str(target),
        ],
        check=True,
    )
    return target.read_bytes()


def _build_test_client(tmp_path, *, asr_provider: str = "none", sarvam_key: str = "") -> TestClient:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_fks(dbapi_conn, _):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    test_settings = Settings(
        _env_file=None,
        database_url="sqlite://",
        evidence_storage_dir=str(tmp_path / "evidence"),
        asr_provider=asr_provider,
        sarvam_api_key=sarvam_key,
        llm_provider="none",
        gemini_api_key="",
    )

    def _override_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_settings] = lambda: test_settings
    return TestClient(app)


def test_http_voice_workflow_upload_playback_consent_transcribe_edit_and_delete(tmp_path, monkeypatch):
    """End-to-end HTTP test matching frontend calls: WebM upload, stream playback, consent, Sarvam ASR, edit, delete."""
    sarvam_calls = 0

    def sarvam_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal sarvam_calls
        sarvam_calls += 1
        return httpx.Response(
            200,
            json={
                "request_id": "req-ui-001",
                "transcript": "My Swiggy weekly payout is 850 rupees short.",
                "language_code": "en-IN",
                "language_probability": 0.98,
            },
        )

    mock_transport = httpx.MockTransport(sarvam_handler)
    orig_init = SarvamASRProvider.__init__

    def _patched_init(self, settings, *, transport=None, timeout_seconds=30.0):
        orig_init(self, settings, transport=mock_transport, timeout_seconds=timeout_seconds)

    monkeypatch.setattr(SarvamASRProvider, "__init__", _patched_init)

    client = _build_test_client(tmp_path, asr_provider="sarvam", sarvam_key="sk_test_sarvam_key")
    try:
        caps = client.get("/api/capabilities")
        assert caps.status_code == 200
        caps_json = caps.json()
        assert caps_json["max_upload_mb"] == 10
        assert caps_json["max_audio_duration_seconds"] == 30.0
        assert caps_json["asr"]["configured"] is True

        # Voice-first auto-draft case creation (platform='other', details={})
        created = client.post("/api/cases", json={"category": "payment_discrepancy", "platform": "other", "details": {}})
        assert created.status_code == 201
        case_id = created.json()["case"]["id"]

        # Browser WebM/Opus upload
        webm_bytes = _make_webm_opus_bytes(tmp_path)
        uploaded = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("voice-grievance.webm", webm_bytes, "audio/webm;codecs=opus")},
        )
        assert uploaded.status_code == 201
        rec = uploaded.json()["recording"]
        rec_id = rec["id"]
        assert rec["mime_type"] == "audio/webm"
        assert rec["transcription_status"] == "pending"

        # Playback stream works
        audio_resp = client.get(f"/api/cases/{case_id}/voice/{rec_id}/audio")
        assert audio_resp.status_code == 200
        assert audio_resp.content == webm_bytes

        # Consent required before Sarvam call
        no_consent = client.post(
            f"/api/cases/{case_id}/voice/{rec_id}/transcribe",
            json={"consent_granted": False},
        )
        assert no_consent.status_code == 400
        assert sarvam_calls == 0

        # Grant consent -> Sarvam transcription succeeds once
        transcribed = client.post(
            f"/api/cases/{case_id}/voice/{rec_id}/transcribe",
            json={"consent_granted": True, "language_code": "en-IN"},
        )
        assert transcribed.status_code == 200
        assert sarvam_calls == 1
        t_rec = transcribed.json()["recording"]
        assert t_rec["raw_transcript"] == "My Swiggy weekly payout is 850 rupees short."
        assert t_rec["transcript_origin"] == "sarvam_asr"
        assert t_rec["transcription_status"] == "completed"

        # Worker saves edited transcript -> original raw_transcript remains immutable
        edited = client.patch(
            f"/api/cases/{case_id}/voice/{rec_id}/transcript",
            json={"worker_edited_transcript": "My Swiggy weekly payout for last week is 850 rupees short."},
        )
        assert edited.status_code == 200
        e_rec = edited.json()["recording"]
        assert e_rec["raw_transcript"] == "My Swiggy weekly payout is 850 rupees short."
        assert e_rec["worker_edited_transcript"] == "My Swiggy weekly payout for last week is 850 rupees short."
        assert e_rec["transcript_origin"] == "sarvam_asr_with_worker_edits"
        # Ensure neither Gemini extraction nor auto-confirmation ran
        assert edited.json()["case"]["status"] == "draft"
        assert edited.json()["case"]["confirmed_details"] is None

        # Delete recording removes file cleanly
        deleted = client.delete(f"/api/cases/{case_id}/voice/{rec_id}")
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] is True
        assert deleted.json()["case"]["voice_recordings"] == []
        assert client.get(f"/api/cases/{case_id}/voice/{rec_id}/audio").status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_http_voice_keyless_mode_and_validation_and_error_responses(tmp_path, monkeypatch):
    """Keyless mode allows recording/playback/manual text without fake AI transcripts; HTTP limits/errors map cleanly."""
    client = _build_test_client(tmp_path, asr_provider="none", sarvam_key="")
    try:
        caps = client.get("/api/capabilities").json()
        assert caps["asr"]["configured"] is False
        assert caps["asr"]["status_code"] == "key_not_set"

        case_id = client.post("/api/cases", json={"category": "payment_discrepancy", "platform": "other", "details": {}}).json()["case"]["id"]

        # Reject invalid/unsupported audio
        bad_audio = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("fake.webm", b"not real audio", "audio/webm")},
        )
        assert bad_audio.status_code == 400

        # Reject >30s audio
        long_wav = _make_wav_bytes(tmp_path, name="long.wav", duration_sec=31.0)
        too_long = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("long.wav", long_wav, "audio/wav")},
        )
        assert too_long.status_code == 400
        assert "30-second" in too_long.json()["detail"]

        # Reject >10 MB payload
        too_big = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("big.wav", b"0" * (10 * 1024 * 1024 + 10), "audio/wav")},
        )
        assert too_big.status_code == 400
        assert "10 MB" in too_big.json()["detail"]

        # Valid recording still works in keyless mode + manual transcript marked manual_worker_entry
        wav = _make_wav_bytes(tmp_path)
        rec_id = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("valid.wav", wav, "audio/wav")},
        ).json()["recording"]["id"]

        keyless_transcribe = client.post(
            f"/api/cases/{case_id}/voice/{rec_id}/transcribe",
            json={"consent_granted": True},
        )
        assert keyless_transcribe.status_code == 200
        k_rec = keyless_transcribe.json()["recording"]
        assert k_rec["transcription_status"] == "key_not_set"
        assert k_rec["raw_transcript"] is None

        manual = client.patch(
            f"/api/cases/{case_id}/voice/{rec_id}/transcript",
            json={"worker_edited_transcript": "Worker typed note without Sarvam key."},
        )
        assert manual.status_code == 200
        m_rec = manual.json()["recording"]
        assert m_rec["raw_transcript"] is None
        assert m_rec["transcript_origin"] == "manual_worker_entry"
    finally:
        app.dependency_overrides.clear()

    # Verify HTTP error JSON format for credits/rate limit/auth/timeout/empty transcript
    orig_init = SarvamASRProvider.__init__

    def _timeout_transport(_req: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("Timed out")

    error_cases = [
        (httpx.MockTransport(lambda _req: httpx.Response(402, json={"error": {"message": "No credits"}})), 402, "insufficient_credits"),
        (httpx.MockTransport(lambda _req: httpx.Response(429, json={"error": {"message": "Rate limited"}})), 429, "rate_limited"),
        (httpx.MockTransport(lambda _req: httpx.Response(401, json={"error": {"message": "Bad key"}})), 502, "provider_auth_error"),
        (httpx.MockTransport(_timeout_transport), 504, "provider_timeout"),
        (httpx.MockTransport(lambda _req: httpx.Response(200, json={"transcript": "   "})), 422, "empty_transcript"),
    ]
    for err_transport, expected_http, expected_code in error_cases:
        monkeypatch.setattr(
            SarvamASRProvider,
            "__init__",
            lambda self, settings, *, transport=None, timeout_seconds=30.0, t=err_transport, oi=orig_init: oi(
                self, settings, transport=t, timeout_seconds=timeout_seconds
            ),
        )
        c2 = _build_test_client(tmp_path / expected_code, asr_provider="sarvam", sarvam_key="sk_test_sarvam_key")
        try:
            cid = c2.post("/api/cases", json={"category": "payment_discrepancy", "platform": "other", "details": {}}).json()["case"]["id"]
            rid = c2.post(
                f"/api/cases/{cid}/voice",
                files={"file": ("ok.wav", _make_wav_bytes(tmp_path, f"{expected_code}.wav"), "audio/wav")},
            ).json()["recording"]["id"]
            resp = c2.post(f"/api/cases/{cid}/voice/{rid}/transcribe", json={"consent_granted": True})
            assert resp.status_code == expected_http
            body = resp.json()
            assert body["error_code"] == expected_code
            assert body["recording"]["transcription_status"] == "failed"
        finally:
            app.dependency_overrides.clear()
