"""Google Gemini structured extraction adapter (gemini-3.8-flash)."""

from app.adapters.base import ProviderCapability
from app.core.config import Settings


class GeminiLLMProvider:
    """Extensible Gemini structured-extraction adapter (implemented in Phase 3)."""

    name = "gemini"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def capability(self) -> ProviderCapability:
        """Return Gemini LLM readiness without exposing secrets."""
        selected = self._settings.llm_provider.strip().lower()
        if selected != self.name:
            return ProviderCapability(
                provider=selected or "none",
                configured=False,
                status_code="key_not_set" if not self._settings.has_gemini_key else "provider_disabled",
                status_message=(
                    "LLM API key not set (structured extraction requires manual review/demo mode)"
                    if not self._settings.has_gemini_key
                    else f"LLM provider set to '{selected}'"
                ),
                model=self._settings.gemini_model,
            )

        if not self._settings.has_gemini_key:
            return ProviderCapability(
                provider=self.name,
                configured=False,
                status_code="key_not_set",
                status_message="Gemini API key not set",
                model=self._settings.gemini_model,
            )

        return ProviderCapability(
            provider=self.name,
            configured=True,
            status_code="configured",
            status_message=f"Gemini extraction ready ({self._settings.gemini_model})",
            model=self._settings.gemini_model,
        )
