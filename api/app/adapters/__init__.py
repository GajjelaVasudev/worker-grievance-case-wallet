"""Extensible provider adapter interfaces and implementations."""

from app.adapters.base import (
    ASRProvider,
    ASRProviderError,
    ExtractionResult,
    LLMProvider,
    LLMProviderError,
    ProviderCapability,
    TranscriptionResult,
)
from app.adapters.gemini_llm import GeminiLLMProvider
from app.adapters.sarvam_asr import SarvamASRProvider

__all__ = [
    "ASRProvider",
    "ASRProviderError",
    "ExtractionResult",
    "LLMProvider",
    "LLMProviderError",
    "ProviderCapability",
    "TranscriptionResult",
    "SarvamASRProvider",
    "GeminiLLMProvider",
]
