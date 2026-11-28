"""Google Gemini structured extraction adapter (gemini-3.8-flash)."""

import json

import httpx

from app.adapters.base import ExtractionResult, LLMProviderError, ProviderCapability
from app.core.config import Settings

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _quoted_string_schema(description: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": ["string", "null"]},
            "source_quote": {"type": ["string", "null"]},
        },
        "required": ["value", "source_quote"],
        "additionalProperties": False,
        "description": description,
    }


def _quoted_number_schema(description: str) -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": ["number", "null"]},
            "source_quote": {"type": ["string", "null"]},
        },
        "required": ["value", "source_quote"],
        "additionalProperties": False,
        "description": description,
    }


PAYMENT_EXTRACTION_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "platform": {
            "type": "object",
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "enum": ["swiggy", "zomato", "rapido", "uber", "other", None],
                },
                "source_quote": {"type": ["string", "null"]},
            },
            "required": ["value", "source_quote"],
            "additionalProperties": False,
        },
        "grievance_category": {
            "type": "object",
            "properties": {
                "value": {
                    "type": ["string", "null"],
                    "enum": ["payment_discrepancy", None],
                },
                "source_quote": {"type": ["string", "null"]},
            },
            "required": ["value", "source_quote"],
            "additionalProperties": False,
        },
        "short_issue_summary": _quoted_string_schema(
            "Short summary of the payment or incentive discrepancy explicitly stated in the transcript."
        ),
        "disputed_amount": _quoted_number_schema(
            "Explicitly stated disputed or missing payout/incentive amount. Do NOT calculate or infer."
        ),
        "currency": _quoted_string_schema(
            "Explicitly stated currency (e.g. INR, rupees, ₹). Leave null if not stated."
        ),
        "relevant_period": _quoted_string_schema(
            "Explicitly stated date, week, day, or payout cycle text."
        ),
        "work_completed": _quoted_string_schema(
            "Work, deliveries, rides, or orders explicitly reported as completed."
        ),
        "expected_payment_amount": _quoted_number_schema(
            "Expected payment or incentive amount only if explicitly stated."
        ),
        "actual_payment_amount": _quoted_number_schema(
            "Actual payment received only if explicitly stated."
        ),
        "incentive_scheme_name": _quoted_string_schema(
            "Incentive, bonus, or guarantee scheme name if explicitly stated."
        ),
        "work_city": _quoted_string_schema("City or work zone if explicitly stated."),
        "prior_ticket_id": _quoted_string_schema("Support ticket or reference ID if explicitly stated."),
        "previous_support_contacts": _quoted_string_schema(
            "Previous contact attempts with support if explicitly stated."
        ),
        "support_responses": _quoted_string_schema(
            "Replies or lack of response from support if explicitly stated."
        ),
        "missing_information": {
            "type": "array",
            "items": {"type": "string"},
        },
        "suggested_followup_questions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": [
        "platform",
        "grievance_category",
        "short_issue_summary",
        "disputed_amount",
        "currency",
        "relevant_period",
        "work_completed",
        "expected_payment_amount",
        "actual_payment_amount",
        "incentive_scheme_name",
        "work_city",
        "prior_ticket_id",
        "previous_support_contacts",
        "support_responses",
        "missing_information",
        "suggested_followup_questions",
    ],
    "additionalProperties": False,
}

_SYSTEM_INSTRUCTION = (
    "You are extracting factual grievance details from a worker-approved transcript for a "
    "payment/incentive discrepancy case. Rules:\n"
    "1. Extract ONLY facts explicitly stated in the transcript.\n"
    "2. Never infer, calculate, or guess missing amounts, dates, platform policies, or events. "
    "Set unknown values and their source_quote to null.\n"
    "3. For every non-null value, source_quote MUST be an exact, verbatim substring copied from "
    "the transcript without paraphrasing or changing script.\n"
    "4. Do not rewrite or correct the transcript.\n"
    "5. List missing facts in missing_information and helpful clarification questions in suggested_followup_questions."
)


def _sanitize_error_detail(raw_text: str, secret_key: str) -> str:
    cleaned = (raw_text or "").strip()
    if secret_key:
        cleaned = cleaned.replace(secret_key, "[REDACTED]")
    return cleaned[:240]


class GeminiLLMProvider:
    """Gemini structured-extraction adapter using generateContent + responseJsonSchema."""

    name = "gemini"

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

    def extract_payment_discrepancy(self, *, approved_transcript: str) -> ExtractionResult:
        """Send worker-approved transcript to Gemini with strict JSON Schema output."""
        selected = self._settings.llm_provider.strip().lower()
        if selected != self.name or not self._settings.has_gemini_key:
            raise LLMProviderError(
                error_code="key_not_set",
                user_message="Gemini API key is not configured.",
                http_status=503,
                retryable=False,
            )

        cleaned_transcript = (approved_transcript or "").strip()
        if not cleaned_transcript:
            raise LLMProviderError(
                error_code="empty_transcript",
                user_message="Approved transcript is empty.",
                http_status=400,
                retryable=False,
            )

        model = (self._settings.gemini_model or "gemini-3.8-flash").strip()
        endpoint = f"{GEMINI_API_BASE}/{model}:generateContent"
        api_key = self._settings.gemini_api_key.strip()

        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        }
        payload = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_INSTRUCTION}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Extract structured payment/incentive discrepancy details from this "
                                "worker-approved transcript:\n\n"
                                f"{cleaned_transcript}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseJsonSchema": PAYMENT_EXTRACTION_JSON_SCHEMA,
            },
        }

        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout_seconds) as client:
                response = client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                error_code="provider_timeout",
                user_message="Structured extraction request to Gemini timed out.",
                http_status=504,
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise LLMProviderError(
                error_code="network_error",
                user_message="Network failure while contacting Gemini extraction service.",
                http_status=502,
                retryable=True,
            ) from exc

        if response.status_code != 200:
            raise self._map_http_error(response, api_key)

        try:
            envelope = response.json()
        except ValueError as exc:
            raise LLMProviderError(
                error_code="malformed_response",
                user_message="Gemini returned an unreadable non-JSON response.",
                http_status=502,
                retryable=True,
            ) from exc

        try:
            candidates = envelope["candidates"]
            text_payload = candidates[0]["content"]["parts"][0]["text"]
            parsed = json.loads(text_payload)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise LLMProviderError(
                error_code="invalid_json_output",
                user_message="Gemini returned malformed or invalid JSON extraction output.",
                http_status=502,
                retryable=True,
            ) from exc

        if not isinstance(parsed, dict):
            raise LLMProviderError(
                error_code="invalid_json_output",
                user_message="Gemini extraction output must be a JSON object.",
                http_status=502,
                retryable=True,
            )

        return ExtractionResult(
            provider=self.name,
            model=model,
            raw_payload=parsed,
        )

    @staticmethod
    def _map_http_error(response: httpx.Response, api_key: str) -> LLMProviderError:
        status_code = response.status_code
        detail = ""
        try:
            err_json = response.json()
            if isinstance(err_json, dict) and isinstance(err_json.get("error"), dict):
                detail = _sanitize_error_detail(str(err_json["error"].get("message") or ""), api_key)
        except ValueError:
            detail = _sanitize_error_detail(response.text, api_key)

        if status_code in (401, 403):
            return LLMProviderError(
                error_code="provider_auth_error",
                user_message="Gemini authentication failed. Verify the configured API key on the server.",
                http_status=502,
                retryable=False,
            )
        if status_code == 429:
            return LLMProviderError(
                error_code="rate_limited",
                user_message="Gemini quota or rate limit reached (HTTP 429). Please wait or complete details manually.",
                http_status=429,
                retryable=True,
            )
        if status_code in (400, 422):
            suffix = f" ({detail})" if detail else ""
            return LLMProviderError(
                error_code="invalid_request",
                user_message=f"Gemini rejected the extraction request{suffix}.",
                http_status=400,
                retryable=False,
            )
        if status_code >= 500:
            return LLMProviderError(
                error_code="provider_server_error",
                user_message="Gemini service returned an upstream server error.",
                http_status=502,
                retryable=True,
            )
        return LLMProviderError(
            error_code="provider_error",
            user_message=f"Gemini extraction failed with HTTP {status_code}.",
            http_status=502,
            retryable=False,
        )
