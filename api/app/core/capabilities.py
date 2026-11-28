"""Runtime capability inspection for transparent no-key operation."""

from dataclasses import asdict

from app.adapters.base import ProviderCapability
from app.adapters.gemini_llm import GeminiLLMProvider
from app.adapters.sarvam_asr import SARVAM_SUPPORTED_MIME_TYPES, SarvamASRProvider
from app.core.config import Settings


def _resolve_asr_capability(settings: Settings) -> ProviderCapability:
    selected = settings.asr_provider.strip().lower()
    if selected == "sarvam":
        return SarvamASRProvider(settings).capability()
    if selected in {"", "none"}:
        return ProviderCapability(
            provider="none",
            configured=False,
            status_code="key_not_set",
            status_message="Speech-to-text API key not set (audio recording saved locally; use Demo/Manual transcript fallback)",
            model=None,
        )
    return ProviderCapability(
        provider=selected,
        configured=False,
        status_code="unsupported_provider",
        status_message=f"ASR provider '{selected}' is not registered",
        model=None,
    )


def _resolve_llm_capability(settings: Settings) -> ProviderCapability:
    selected = settings.llm_provider.strip().lower()
    if selected == "gemini":
        return GeminiLLMProvider(settings).capability()
    if selected in {"", "none"}:
        return ProviderCapability(
            provider="none",
            configured=False,
            status_code="key_not_set",
            status_message="Structured extraction API key not set (manual review or Demo Sample mode available)",
            model=None,
        )
    return ProviderCapability(
        provider=selected,
        configured=False,
        status_code="unsupported_provider",
        status_message=f"LLM provider '{selected}' is not registered",
        model=None,
    )


def build_capabilities_payload(settings: Settings) -> dict:
    """Return structured, non-secret capability flags for the UI."""
    asr_cap = _resolve_asr_capability(settings)
    llm_cap = _resolve_llm_capability(settings)

    return {
        "app_name": settings.app_name,
        "phase": "phase_2_core_grievance_engine",
        "wallet_mode": "single_local_wallet",
        "public_demo_mode": settings.public_demo_mode,
        "hero_category": "payment_discrepancy",
        "supported_platforms": ["swiggy", "zomato", "rapido", "uber"],
        "supported_audio_mime_types": list(SARVAM_SUPPORTED_MIME_TYPES),
        "max_upload_mb": settings.max_upload_mb,
        "max_audio_duration_seconds": settings.max_audio_duration_seconds,
        "asr": asdict(asr_cap),
        "llm": asdict(llm_cap),
    }
