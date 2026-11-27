"""Application configuration loaded safely without requiring a .env file."""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


def _is_placeholder_or_empty(value: str | None) -> bool:
    """Return True when an API key is unset or still holds an example placeholder."""
    if not value:
        return True
    cleaned = value.strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    return lowered.startswith("your-") or lowered in {"changeme", "placeholder", "none"}


class Settings(BaseSettings):
    """Runtime settings with zero-key defaults for Docker Compose startup."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Worker Grievance Case Wallet"
    app_env: str = "development"
    public_demo_mode: bool = False

    database_url: str = "postgresql+psycopg://wallet:wallet@db:5432/grievance_wallet"
    evidence_storage_dir: str = "/data/evidence"
    max_upload_mb: int = 10

    asr_provider: str = "none"
    sarvam_api_key: str = ""
    sarvam_stt_model: str = "saaras:v4"
    sarvam_stt_mode: str = "codemix"

    llm_provider: str = "none"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.8-flash"

    @property
    def has_sarvam_key(self) -> bool:
        """True only when a non-placeholder Sarvam API key is configured."""
        return not _is_placeholder_or_empty(self.sarvam_api_key)

    @property
    def has_gemini_key(self) -> bool:
        """True only when a non-placeholder Gemini API key is configured."""
        return not _is_placeholder_or_empty(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    """Return cached Settings instance."""
    return Settings()
