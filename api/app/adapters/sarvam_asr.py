"""Sarvam AI Speech-to-Text adapter (saaras:v4)."""

import httpx

from app.adapters.base import ASRProviderError, ProviderCapability, TranscriptionResult
from app.core.config import Settings

SARVAM_STT_ENDPOINT = "https://api.sarvam.ai/speech-to-text"
SARVAM_VALID_MODES: tuple[str, ...] = ("transcribe", "translate", "verbatim", "translit", "codemix")

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


def _sanitize_error_detail(raw_text: str, secret_key: str) -> str:
    """Ensure API key or excessive upstream body is never leaked in user-facing messages."""
    cleaned = (raw_text or "").strip()
    if secret_key:
        cleaned = cleaned.replace(secret_key, "[REDACTED]")
    return cleaned[:240]


class SarvamASRProvider:
    """Sarvam STT adapter targeting POST https://api.sarvam.ai/speech-to-text (saaras:v4)."""

    name = "sarvam"
    supported_mime_types = SARVAM_SUPPORTED_MIME_TYPES

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._timeout_seconds = timeout_seconds

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

    def transcribe_audio(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        mime_type: str,
        language_code: str | None = None,
    ) -> TranscriptionResult:
        """Send a single validated <=30s audio clip to Sarvam saaras:v4 without silent retries."""
        selected = self._settings.asr_provider.strip().lower()
        if selected != self.name or not self._settings.has_sarvam_key:
            raise ASRProviderError(
                error_code="key_not_set",
                user_message="Sarvam API key is not configured.",
                http_status=503,
                retryable=False,
            )

        normalized_mime = (mime_type or "").split(";")[0].strip().lower()
        if normalized_mime not in self.supported_mime_types:
            raise ASRProviderError(
                error_code="unsupported_audio",
                user_message=f"Audio MIME type '{normalized_mime or 'unknown'}' is not supported by Sarvam STT.",
                http_status=400,
                retryable=False,
            )

        if not audio_bytes:
            raise ASRProviderError(
                error_code="unsupported_audio",
                user_message="Audio recording is empty.",
                http_status=400,
                retryable=False,
            )

        model = (self._settings.sarvam_stt_model or "saaras:v4").strip()
        mode = (self._settings.sarvam_stt_mode or "codemix").strip().lower()
        if mode not in SARVAM_VALID_MODES:
            mode = "codemix"

        form_data: dict[str, str] = {"model": model, "mode": mode}
        if language_code and language_code.strip():
            form_data["language_code"] = language_code.strip()

        api_key = self._settings.sarvam_api_key.strip()
        headers = {"api-subscription-key": api_key}
        files = {"file": (filename or "recording.webm", audio_bytes, normalized_mime)}

        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.post(
                    SARVAM_STT_ENDPOINT,
                    headers=headers,
                    data=form_data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ASRProviderError(
                error_code="provider_timeout",
                user_message="Transcription request to Sarvam timed out. Your audio recording is still saved.",
                http_status=504,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ASRProviderError(
                error_code="network_error",
                user_message="Network failure while contacting Sarvam transcription service.",
                http_status=502,
                retryable=True,
            ) from exc

        if response.status_code != 200:
            raise self._map_http_error(response, api_key)

        try:
            body = response.json()
        except ValueError as exc:
            raise ASRProviderError(
                error_code="malformed_response",
                user_message="Sarvam returned an unreadable non-JSON response.",
                http_status=502,
                retryable=True,
            ) from exc

        if not isinstance(body, dict):
            raise ASRProviderError(
                error_code="malformed_response",
                user_message="Sarvam returned an unexpected response structure.",
                http_status=502,
                retryable=True,
            )

        transcript = body.get("transcript")
        if not isinstance(transcript, str) or not transcript.strip():
            raise ASRProviderError(
                error_code="empty_transcript",
                user_message="Sarvam returned an empty transcript. You may retry or enter text manually.",
                http_status=422,
                retryable=True,
            )

        raw_prob = body.get("language_probability")
        lang_prob = float(raw_prob) if isinstance(raw_prob, (int, float)) else None
        raw_lang = body.get("language_code")
        detected_lang = str(raw_lang).strip() if isinstance(raw_lang, str) and raw_lang.strip() else None
        raw_req_id = body.get("request_id")
        req_id = str(raw_req_id).strip() if isinstance(raw_req_id, str) and raw_req_id.strip() else None

        return TranscriptionResult(
            provider=self.name,
            model=model,
            mode=mode,
            transcript=transcript.strip(),
            request_id=req_id,
            language_code=detected_lang,
            language_probability=lang_prob,
        )

    @staticmethod
    def _map_http_error(response: httpx.Response, api_key: str) -> ASRProviderError:
        status_code = response.status_code
        provider_code = None
        provider_msg = ""
        try:
            err_json = response.json()
            if isinstance(err_json, dict) and isinstance(err_json.get("error"), dict):
                provider_code = err_json["error"].get("code")
                provider_msg = _sanitize_error_detail(str(err_json["error"].get("message") or ""), api_key)
        except ValueError:
            provider_msg = _sanitize_error_detail(response.text, api_key)

        if status_code in (401, 403):
            return ASRProviderError(
                error_code="provider_auth_error",
                user_message="Sarvam authentication failed. Verify the configured API key on the server.",
                http_status=502,
                retryable=False,
            )
        if status_code == 402:
            return ASRProviderError(
                error_code="insufficient_credits",
                user_message="Sarvam transcription credits are exhausted or unavailable (HTTP 402).",
                http_status=402,
                retryable=False,
            )
        if status_code == 413:
            return ASRProviderError(
                error_code="payload_too_large",
                user_message="Audio file exceeded Sarvam's payload size limit (HTTP 413).",
                http_status=413,
                retryable=False,
            )
        if status_code == 429:
            return ASRProviderError(
                error_code="rate_limited",
                user_message="Sarvam rate limit reached (HTTP 429). Please wait before trying again.",
                http_status=429,
                retryable=True,
            )
        if status_code in (400, 422):
            detail = f" ({provider_code}: {provider_msg})" if provider_code and provider_msg else ""
            return ASRProviderError(
                error_code="invalid_request",
                user_message=f"Sarvam rejected the audio request{detail}.",
                http_status=400,
                retryable=False,
            )
        if status_code >= 500:
            return ASRProviderError(
                error_code="provider_server_error",
                user_message="Sarvam transcription service returned a server error. Please try again later.",
                http_status=502,
                retryable=True,
            )
        return ASRProviderError(
            error_code="provider_error",
            user_message=f"Sarvam transcription failed with HTTP {status_code}.",
            http_status=502,
            retryable=False,
        )
