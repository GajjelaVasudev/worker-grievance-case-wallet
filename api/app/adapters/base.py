"""Abstract interfaces for independently configurable ASR and LLM providers."""

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


class ASRProvider(Protocol):
    """Speech-to-text adapter contract."""

    name: str
    supported_mime_types: tuple[str, ...]

    def capability(self) -> ProviderCapability:
        """Report readiness and whether an API key is configured."""
        ...


class LLMProvider(Protocol):
    """Structured grievance extraction adapter contract."""

    name: str

    def capability(self) -> ProviderCapability:
        """Report readiness and whether an API key is configured."""
        ...
