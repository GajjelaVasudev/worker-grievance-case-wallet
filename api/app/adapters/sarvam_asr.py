"""Sarvam AI Speech-to-Text adapter (Saaras v4)."""

from app.adapters.base import ProviderCapability
from app.core.config import Settings

SARVAM_SUPPORTED_MIME_TYPES: tuple[str, ...] = (
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/wave",
    "audio/mpeg",
    "audio/mp3",
    "audio/ogg",
    "audio/opus",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/flac",
    "audio/aac",
)


class SarvamASRProvider:
    """Extensible Sarvam STT adapter targeting saaras:v4 (implemented in Phase 3)."""

    name = "sarvam"
    supported_mime_types = SARVAM_SUPPORTED_MIME_TYPES

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def capability(self) -> ProviderCapability:
        """Return Sarvam ASR readiness without exposing secrets."""
        selected = self._settings.asr_provider.strip().lower()
        if selected != self.name:
            return ProviderCapability(
                provider=selected or "none",
                configured=False,
                status_code="key_not_set" if not self._settings.has_sarvam_key else "provider_disabled",
                status_message=(
                    "ASR API key not set (running without cloud transcription)"
                    if not self._settings.has_sarvam_key
                    else f"ASR provider set to '{selected}'"
                ),
                model=self._settings.sarvam_stt_model,
            )

        if not self._settings.has_sarvam_key:
            return ProviderCapability(
                provider=self.name,
                configured=False,
                status_code="key_not_set",
                status_message="Sarvam API key not set",
                model=self._settings.sarvam_stt_model,
            )

        return ProviderCapability(
            provider=self.name,
            configured=True,
            status_code="configured",
            status_message=f"Sarvam STT ready ({self._settings.sarvam_stt_model})",
            model=self._settings.sarvam_stt_model,
        )
