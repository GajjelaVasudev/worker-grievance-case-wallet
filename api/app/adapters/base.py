"""Abstract interfaces and shared result/error types for ASR and LLM providers."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProviderCapability:
    """Transparent status report for a configured AI provider."""

    provider: str
    configured: bool
    status_code: str
    status_message: str
    model: str | None = None


@dataclass(frozen=True)
class TranscriptionResult:
    """Normalized speech-to-text output from an ASR provider."""

    provider: str
    model: str
    mode: str
    transcript: str
    request_id: str | None = None
    language_code: str | None = None
    language_probability: float | None = None


class ASRProviderError(RuntimeError):
    """Safe, key-redacted error raised when speech-to-text fails."""

    def __init__(
        self,
        *,
        error_code: str,
        user_message: str,
        http_status: int = 502,
        retryable: bool = False,
    ) -> None:
        super().__init__(user_message)
        self.error_code = error_code
        self.user_message = user_message
        self.http_status = http_status
        self.retryable = retryable


class ASRProvider(Protocol):
    """Speech-to-text adapter contract."""

    name: str
    supported_mime_types: tuple[str, ...]

    def capability(self) -> ProviderCapability:
        """Report readiness and whether an API key is configured."""
        ...

    def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        mime_type: str,
        language_code: str | None = None,
    ) -> TranscriptionResult:
        """Transcribe an audio clip and return normalized transcript metadata."""
        ...


class LLMProvider(Protocol):
    """Structured grievance extraction adapter contract."""

    name: str

    def capability(self) -> ProviderCapability:
        """Report readiness and whether an API key is configured."""
        ...
