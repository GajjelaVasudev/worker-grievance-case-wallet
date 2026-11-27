"""Validation rules enforcing null-for-unknown, quote provenance, and confirmation gates."""

from app.schemas.case import FieldProvenance, PaymentDiscrepancyDetails

PAYMENT_FIELD_PROMPTS: tuple[tuple[str, str, str], ...] = (
    ("platform", "important", "Which delivery or ride-hailing platform is this payment issue about?"),
    ("issue_description", "important", "Briefly describe what payout or incentive was missed or short-paid."),
    ("payout_cycle_raw", "important", "Which payout cycle or date range is affected?"),
    ("expected_amount_inr", "important", "How much payout or incentive did you expect (in INR)?"),
    ("received_amount_inr", "important", "How much did you actually receive (in INR)?"),
    ("incentive_scheme_name", "helpful", "What was the name or condition of the incentive/guarantee scheme?"),
    ("work_city", "helpful", "Which city or zone were you working in?"),
    ("affected_order_or_trip_ids", "helpful", "Do you have specific order or trip IDs for the missed payout?"),
    ("prior_ticket_id", "helpful", "Have you already raised an in-app support ticket? If yes, what is the ticket ID?"),
)


def detect_missing_payment_fields(details: PaymentDiscrepancyDetails) -> list[dict]:
    """Identify null/unknown fields without inventing values."""
    data = details.model_dump()
    missing = []
    for field_name, importance, prompt in PAYMENT_FIELD_PROMPTS:
        value = data.get(field_name)
        is_missing = value is None or (isinstance(value, list) and len(value) == 0)
        if is_missing:
            missing.append(
                {
                    "field": field_name,
                    "importance": importance,
                    "prompt": prompt,
                }
            )
    return missing


def validate_field_provenance(
    provenance: dict[str, FieldProvenance] | None,
    transcript_context: str | None,
) -> dict[str, dict] | None:
    """Ensure any transcript_quote provenance matches an exact excerpt in the transcript."""
    if not provenance:
        return None

    normalized_transcript = " ".join(transcript_context.split()) if transcript_context else ""
    validated: dict[str, dict] = {}
    for field_name, entry in provenance.items():
        if entry.origin == "transcript_quote":
            if not entry.source_quote or not entry.source_quote.strip():
                raise ValueError(f"Field '{field_name}' with transcript_quote origin requires a non-empty source_quote.")
            if not normalized_transcript:
                raise ValueError(
                    f"Field '{field_name}' claims transcript_quote origin, but no transcript_context was provided."
                )
            normalized_quote = " ".join(entry.source_quote.split())
            if normalized_quote not in normalized_transcript:
                raise ValueError(
                    f"Source quote for '{field_name}' must be an exact excerpt from the transcript."
                )
        validated[field_name] = entry.model_dump()
    return validated


def validate_confirmation_readiness(details: PaymentDiscrepancyDetails) -> None:
    """Require minimum worker-reviewed core facts before confirming a case."""
    if details.platform is None:
        raise ValueError("Platform must be specified before confirming the case.")
    if details.issue_description is None:
        raise ValueError("Issue description must be provided before confirming the case.")
