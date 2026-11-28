"""Phase 3 Task 8 tests: frontend-to-backend Gemini extraction, provenance, conflicts, corrections, and confirmation."""

import json
import subprocess

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.adapters.gemini_llm import GeminiLLMProvider
from app.adapters.sarvam_asr import SarvamASRProvider
from app.core.config import Settings, get_settings
from app.core.database import Base, get_db
from app.main import app
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.voice import CaseExtractionDraft, VoiceRecording  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401


def _make_wav_bytes(tmp_path, name: str = "t8.wav") -> bytes:
    target = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1.1", str(target)],
        check=True,
    )
    return target.read_bytes()


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


def _gemini_envelope(payload: dict) -> dict:
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(payload)}], "role": "model"},
                "finishReason": "STOP",
            }
        ]
    }


def _build_client(
    tmp_path,
    *,
    asr_provider: str = "none",
    sarvam_key: str = "",
    llm_provider: str = "none",
    gemini_key: str = "",
) -> TestClient:
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
        llm_provider=llm_provider,
        gemini_api_key=gemini_key,
        gemini_model="gemini-3.6-flash",
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


def test_http_end_to_end_extraction_grounding_conflicts_corrections_and_confirmation(tmp_path, monkeypatch):
    """Verify full frontend-to-backend voice -> Sarvam -> Gemini -> edit -> conflict -> confirm flow."""
    sarvam_transport = httpx.MockTransport(
        lambda _req: httpx.Response(
            200,
            json={
                "request_id": "req-t8-1",
                "transcript": "Swiggy payout is 850 rupees short this week.",
                "language_code": "en-IN",
                "language_probability": 0.99,
            },
        )
    )
    orig_sarvam_init = SarvamASRProvider.__init__
    monkeypatch.setattr(
        SarvamASRProvider,
        "__init__",
        lambda self, settings, *, transport=None, timeout_seconds=30.0: orig_sarvam_init(
            self, settings, transport=sarvam_transport, timeout_seconds=timeout_seconds
        ),
    )

    gemini_prompts: list[str] = []

    def gemini_handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.read())
        prompt_text = body["contents"][0]["parts"][0]["text"]
        gemini_prompts.append(prompt_text)

        payload = _blank_llm_payload()
        if len(gemini_prompts) == 1:
            payload.update(
                {
                    "platform": {"value": "swiggy", "source_quote": "Swiggy"},
                    "grievance_category": {"value": "payment_discrepancy", "source_quote": "850 rupees short"},
                    "short_issue_summary": {
                        "value": "Swiggy payout is 850 rupees short this week",
                        "source_quote": "Swiggy payout is 850 rupees short this week",
                    },
                    "disputed_amount": {"value": 850, "source_quote": "850 rupees"},
                    "currency": {"value": "rupees", "source_quote": "850 rupees"},
                    "relevant_period": {"value": "this week", "source_quote": "this week"},
                    # Deliberate grounding violation (value 9999 does not match quote 850 rupees)
                    "expected_payment_amount": {"value": 9999, "source_quote": "850 rupees"},
                    "missing_information": ["Work city"],
                    "suggested_followup_questions": ["Which city did you deliver in?"],
                }
            )
        else:
            payload.update(
                {
                    "platform": {"value": "swiggy", "source_quote": "Swiggy"},
                    "disputed_amount": {"value": 1200, "source_quote": "1200 rupees"},
                    "currency": {"value": "rupees", "source_quote": "1200 rupees"},
                    "work_city": {"value": "Bengaluru", "source_quote": "Bengaluru"},
                }
            )
        return httpx.Response(200, json=_gemini_envelope(payload))

    gemini_transport = httpx.MockTransport(gemini_handler)
    orig_gemini_init = GeminiLLMProvider.__init__
    monkeypatch.setattr(
        GeminiLLMProvider,
        "__init__",
        lambda self, settings, *, transport=None, timeout_seconds=30.0: orig_gemini_init(
            self, settings, transport=gemini_transport, timeout_seconds=timeout_seconds
        ),
    )

    client = _build_client(
        tmp_path,
        asr_provider="sarvam",
        sarvam_key="sk_test_sarvam",
        llm_provider="gemini",
        gemini_key="AIzaSyTestGeminiKey",
    )
    try:
        # 1. Create draft case + upload recording #1 + transcribe via Sarvam + edit worker transcript
        created = client.post("/api/cases", json={"category": "payment_discrepancy", "platform": "other", "details": {}})
        assert created.status_code == 201
        case_id = created.json()["case"]["id"]

        wav = _make_wav_bytes(tmp_path)
        rec1_id = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("r1.wav", wav, "audio/wav")},
        ).json()["recording"]["id"]

        transcribed = client.post(
            f"/api/cases/{case_id}/voice/{rec1_id}/transcribe",
            json={"consent_granted": True},
        )
        assert transcribed.status_code == 200
        assert transcribed.json()["recording"]["raw_transcript"] == "Swiggy payout is 850 rupees short this week."

        edited_tr = "Swiggy payout is 850 rupees short this week after completing 20 orders."
        client.patch(
            f"/api/cases/{case_id}/voice/{rec1_id}/transcript",
            json={"worker_edited_transcript": edited_tr},
        )

        # 2. Consent and worker approval gates enforced before Gemini call
        no_consent = client.post(
            f"/api/cases/{case_id}/voice/{rec1_id}/extract",
            json={
                "consent_granted": False,
                "transcript_approved_by_worker": True,
                "transcript_layer": "worker_edited_transcript",
            },
        )
        assert no_consent.status_code == 400
        no_approval = client.post(
            f"/api/cases/{case_id}/voice/{rec1_id}/extract",
            json={
                "consent_granted": True,
                "transcript_approved_by_worker": False,
                "transcript_layer": "worker_edited_transcript",
            },
        )
        assert no_approval.status_code == 400
        assert len(gemini_prompts) == 0

        # 3. Run extraction on worker_edited_transcript
        ex1 = client.post(
            f"/api/cases/{case_id}/voice/{rec1_id}/extract",
            json={
                "consent_granted": True,
                "transcript_approved_by_worker": True,
                "transcript_layer": "worker_edited_transcript",
            },
        )
        assert ex1.status_code == 200
        assert len(gemini_prompts) == 1
        assert edited_tr in gemini_prompts[0]

        ex1_body = ex1.json()
        draft1 = ex1_body["draft"]
        case_after_1 = ex1_body["case"]
        assert draft1["llm_model"] == "gemini-3.6-flash"
        assert draft1["extraction_status"] == "completed"
        # Default 'other' replaced cleanly with Swiggy (no false conflict)
        assert draft1["conflict_suggestions"] is None
        assert case_after_1["platform"] == "swiggy"
        assert case_after_1["draft_details"]["discrepancy_amount_inr"] == 850.0
        assert case_after_1["field_provenance"]["discrepancy_amount_inr"]["source_quote"] == "850 rupees"
        # Grounding rejected mismatched expected_amount_inr (9999 vs 850 quote)
        assert case_after_1["draft_details"]["expected_amount_inr"] is None
        assert "expected_amount_inr" in draft1["stripped_ungrounded_fields"]
        assert any(
            m["field"] == "expected_amount_inr" and m["importance"] == "clarification_required"
            for m in case_after_1["missing_fields"]
        )
        assert case_after_1["status"] == "draft"
        assert case_after_1["confirmed_details"] is None

        # 4. Recording #2 produces conflicting disputed amount (1200 vs 850) + fills work_city
        rec2_id = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("r2.wav", wav, "audio/wav")},
        ).json()["recording"]["id"]
        client.patch(
            f"/api/cases/{case_id}/voice/{rec2_id}/transcript",
            json={"worker_edited_transcript": "In Bengaluru my Swiggy missing payout is 1200 rupees."},
        )
        ex2 = client.post(
            f"/api/cases/{case_id}/voice/{rec2_id}/extract",
            json={
                "consent_granted": True,
                "transcript_approved_by_worker": True,
                "transcript_layer": "worker_edited_transcript",
            },
        )
        assert ex2.status_code == 200
        ex2_body = ex2.json()
        assert ex2_body["case"]["draft_details"]["discrepancy_amount_inr"] == 850.0
        assert ex2_body["case"]["draft_details"]["work_city"] == "Bengaluru"
        assert "discrepancy_amount_inr" in ex2_body["draft"]["conflict_suggestions"]
        assert ex2_body["draft"]["conflict_suggestions"]["discrepancy_amount_inr"]["proposed_value"] == 1200.0

        # 5. Worker manually corrects expected_amount_inr & resolves conflict to 1200, preserving provenance distinction
        updated_details = dict(ex2_body["case"]["draft_details"])
        updated_details["expected_amount_inr"] = 2200.0
        updated_details["discrepancy_amount_inr"] = 1200.0
        saved_draft = client.patch(
            f"/api/cases/{case_id}/draft",
            json={
                "platform": "swiggy",
                "details": updated_details,
                "field_provenance": {
                    "platform": {
                        "origin": "llm_extracted",
                        "source_quote": "Swiggy",
                        "voice_recording_id": rec1_id,
                        "transcript_layer": "worker_edited_transcript",
                    },
                    "expected_amount_inr": {"origin": "worker_edited"},
                    "discrepancy_amount_inr": {
                        "origin": "llm_extracted",
                        "source_quote": "1200 rupees",
                        "voice_recording_id": rec2_id,
                        "transcript_layer": "worker_edited_transcript",
                    },
                },
            },
        )
        assert saved_draft.status_code == 200
        sd_case = saved_draft.json()["case"]
        assert sd_case["field_provenance"]["expected_amount_inr"]["origin"] == "worker_edited"
        assert sd_case["field_provenance"]["platform"]["origin"] == "llm_extracted"
        # Both original extraction drafts stay preserved in history
        assert len(sd_case["extraction_drafts"]) == 2

        # 6. Explicit worker confirmation
        confirmed = client.post(
            f"/api/cases/{case_id}/confirm",
            json={
                "confirm": True,
                "platform": "swiggy",
                "details": sd_case["draft_details"],
                "worker_confirmation_note": "Verified against weekly payout sheet",
            },
        )
        assert confirmed.status_code == 200
        conf_case = confirmed.json()["case"]
        assert conf_case["status"] == "confirmed"
        assert conf_case["confirmed_details"]["discrepancy_amount_inr"] == 1200.0

        # 7. Subsequent extraction on raw_transcript MUST NOT overwrite confirmed_details
        ex3 = client.post(
            f"/api/cases/{case_id}/voice/{rec1_id}/extract",
            json={
                "consent_granted": True,
                "transcript_approved_by_worker": True,
                "transcript_layer": "raw_transcript",
            },
        )
        assert ex3.status_code == 200
        assert "completing 20 orders" not in gemini_prompts[-1]
        assert ex3.json()["case"]["status"] == "confirmed"
        assert ex3.json()["case"]["confirmed_details"]["discrepancy_amount_inr"] == 1200.0
    finally:
        app.dependency_overrides.clear()


def test_http_gemini_keyless_extraction_and_manual_confirmation(tmp_path):
    """When Gemini key is absent, extraction reports key_not_set without fabricating values; manual confirmation works."""
    client = _build_client(tmp_path, asr_provider="none", sarvam_key="", llm_provider="none", gemini_key="")
    try:
        caps = client.get("/api/capabilities").json()
        assert caps["llm"]["configured"] is False
        assert caps["llm"]["status_code"] == "key_not_set"

        case_id = client.post("/api/cases", json={"category": "payment_discrepancy", "platform": "other", "details": {}}).json()["case"]["id"]
        rec_id = client.post(
            f"/api/cases/{case_id}/voice",
            files={"file": ("k.wav", _make_wav_bytes(tmp_path, "keyless.wav"), "audio/wav")},
        ).json()["recording"]["id"]
        client.patch(
            f"/api/cases/{case_id}/voice/{rec_id}/transcript",
            json={"worker_edited_transcript": "Uber quest incentive of 600 rupees missing."},
        )

        ex = client.post(
            f"/api/cases/{case_id}/voice/{rec_id}/extract",
            json={
                "consent_granted": True,
                "transcript_approved_by_worker": True,
                "transcript_layer": "worker_edited_transcript",
            },
        )
        assert ex.status_code == 200
        ex_json = ex.json()
        assert ex_json["draft"]["extraction_status"] == "key_not_set"
        assert ex_json["draft"]["extracted_fields"] is None
        assert ex_json["case"]["draft_details"]["discrepancy_amount_inr"] is None

        # Manual confirmation still works cleanly in keyless mode
        conf = client.post(
            f"/api/cases/{case_id}/confirm",
            json={
                "confirm": True,
                "platform": "uber",
                "details": {
                    "platform": "uber",
                    "discrepancy_amount_inr": 600,
                    "currency": "rupees",
                    "issue_description": "Uber quest incentive of 600 rupees missing.",
                },
            },
        )
        assert conf.status_code == 200
        assert conf.json()["case"]["status"] == "confirmed"
        assert conf.json()["case"]["confirmed_details"]["discrepancy_amount_inr"] == 600
    finally:
        app.dependency_overrides.clear()
