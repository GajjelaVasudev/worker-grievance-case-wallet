"""Phase 1 foundation tests covering zero-key config, capabilities, and healthcheck."""

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.health import get_capabilities, health_check
from app.core.capabilities import build_capabilities_payload
from app.core.config import Settings
from app.core.database import Base, DEFAULT_LOCAL_WALLET_ID, ensure_default_worker
from app.main import app
from app.models.worker import WorkerProfile  # noqa: F401


def _build_test_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)
    return TestingSessionLocal()


def test_zero_key_capabilities_never_crash():
    """App settings and capability inspection must succeed with no .env and placeholder keys."""
    settings = Settings(
        _env_file=None,
        asr_provider="sarvam",
        sarvam_api_key="your-sarvam-api-key-here",
        llm_provider="gemini",
        gemini_api_key="your-gemini-api-key-here",
    )
    payload = build_capabilities_payload(settings)
    assert payload["asr"]["configured"] is False
    assert payload["asr"]["status_code"] == "key_not_set"
    assert payload["llm"]["configured"] is False
    assert payload["llm"]["status_code"] == "key_not_set"


def test_configured_provider_capabilities():
    """Configured non-placeholder keys report ready status for Sarvam and Gemini."""
    settings = Settings(
        _env_file=None,
        asr_provider="sarvam",
        sarvam_api_key="sk_test_real_looking_key",
        llm_provider="gemini",
        gemini_api_key="AIzaSyTestExampleKey123",
    )
    payload = build_capabilities_payload(settings)
    assert payload["asr"]["configured"] is True
    assert payload["asr"]["model"] == "saaras:v4"
    assert payload["llm"]["configured"] is True
    assert payload["llm"]["model"] == "gemini-3.8-flash"


def test_health_and_capabilities_routes_without_keys(tmp_path):
    """Health and capabilities handlers succeed with DB + storage and no API keys."""
    openapi_paths = set(app.openapi().get("paths", {}).keys())
    assert "/api/health" in openapi_paths
    assert "/api/capabilities" in openapi_paths

    db = _build_test_session()
    try:
        wallet_id = ensure_default_worker(db, is_demo=False)
        assert wallet_id == DEFAULT_LOCAL_WALLET_ID

        settings = Settings(
            _env_file=None,
            database_url="sqlite://",
            evidence_storage_dir=str(tmp_path),
            asr_provider="none",
            sarvam_api_key="",
            llm_provider="none",
            gemini_api_key="",
        )

        health_response = health_check(db=db, settings=settings)
        assert health_response.status_code == 200
        health_body = json.loads(health_response.body)
        assert health_body["status"] == "ok"
        assert health_body["database"] == "ok"
        assert health_body["storage"] == "ok"
        assert health_body["local_wallet_id"] == DEFAULT_LOCAL_WALLET_ID

        caps_body = get_capabilities(settings=settings)
        assert caps_body["wallet_mode"] == "single_local_wallet"
        assert caps_body["hero_category"] == "payment_discrepancy"
        assert caps_body["asr"]["status_code"] == "key_not_set"
        assert caps_body["llm"]["status_code"] == "key_not_set"
    finally:
        db.close()
