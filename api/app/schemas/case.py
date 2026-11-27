"""Pydantic schemas for Payment/Incentive Discrepancy cases, evidence, timeline, and routing."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

PlatformName = Literal["swiggy", "zomato", "rapido", "uber", "other"]
CaseCategory = Literal["payment_discrepancy"]
EvidenceCategory = Literal[
    "payout_summary",
    "incentive_offer",
    "order_or_trip_records",
    "support_communication",
    "other",
]
TimelineMilestoneType = Literal[
    "worker_reported_submission",
    "evidence_backed_submission",
    "platform_acknowledgement_reported",
    "platform_response_reported",
    "escalation_reported",
    "closed",
    "note_added",
]


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


class PaymentDiscrepancyDetails(BaseModel):
    """Structured fields for Payment/Incentive Discrepancy. Unknowns stay null."""

    platform: PlatformName | None = None
    work_city: str | None = None
    payout_cycle_raw: str | None = None
    payout_cycle_start: str | None = None
    payout_cycle_end: str | None = None
    incentive_scheme_name: str | None = None
    expected_amount_inr: float | None = Field(default=None, ge=0)
    received_amount_inr: float | None = Field(default=None, ge=0)
    discrepancy_amount_inr: float | None = Field(default=None, ge=0)
    affected_order_or_trip_ids: list[str] | None = None
    prior_ticket_id: str | None = None
    issue_description: str | None = None

    @field_validator(
        "work_city",
        "payout_cycle_raw",
        "payout_cycle_start",
        "payout_cycle_end",
        "incentive_scheme_name",
        "prior_ticket_id",
        "issue_description",
        mode="before",
    )
    @classmethod
    def normalize_blank_strings(cls, value: str | None) -> str | None:
        """Normalize empty or whitespace-only strings to null."""
        return _blank_to_none(value)

    @field_validator("payout_cycle_start", "payout_cycle_end")
    @classmethod
    def validate_iso_date_if_present(cls, value: str | None) -> str | None:
        """Ensure normalized cycle dates are valid YYYY-MM-DD when provided."""
        if value is None:
            return None
        date.fromisoformat(value)
        return value

    @field_validator("affected_order_or_trip_ids", mode="before")
    @classmethod
    def normalize_id_list(cls, value: list[str] | None) -> list[str] | None:
        """Keep unknown/empty lists as null rather than fabricating placeholders."""
        if value is None:
            return None
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return cleaned if cleaned else None

    @model_validator(mode="after")
    def validate_cycle_range(self) -> "PaymentDiscrepancyDetails":
        """Ensure cycle end date is not earlier than cycle start date."""
        if self.payout_cycle_start and self.payout_cycle_end:
            start = date.fromisoformat(self.payout_cycle_start)
            end = date.fromisoformat(self.payout_cycle_end)
            if end < start:
                raise ValueError("payout_cycle_end cannot be earlier than payout_cycle_start")
        return self


class FieldProvenance(BaseModel):
    """Provenance record for a draft or extracted field."""

    origin: Literal["worker_input", "transcript_quote", "worker_edited"]
    source_quote: str | None = None


class CreateCaseRequest(BaseModel):
    """Create a new Payment/Incentive Discrepancy draft case."""

    category: str = "payment_discrepancy"
    platform: PlatformName | None = None
    jurisdiction_state: str | None = None
    summary_title: str | None = None
    details: PaymentDiscrepancyDetails = Field(default_factory=PaymentDiscrepancyDetails)
    field_provenance: dict[str, FieldProvenance] | None = None
    transcript_context: str | None = None

    @field_validator("jurisdiction_state", "summary_title", "transcript_context", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        return _blank_to_none(value)


class UpdateDraftRequest(BaseModel):
    """Update draft fields prior to worker confirmation."""

    platform: PlatformName | None = None
    jurisdiction_state: str | None = None
    summary_title: str | None = None
    details: PaymentDiscrepancyDetails
    field_provenance: dict[str, FieldProvenance] | None = None
    transcript_context: str | None = None

    @field_validator("jurisdiction_state", "summary_title", "transcript_context", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        return _blank_to_none(value)


class ConfirmCaseRequest(BaseModel):
    """Explicit worker confirmation required to transition draft -> confirmed."""

    confirm: bool
    platform: PlatformName | None = None
    jurisdiction_state: str | None = None
    summary_title: str | None = None
    details: PaymentDiscrepancyDetails
    worker_confirmation_note: str | None = None

    @field_validator("jurisdiction_state", "summary_title", "worker_confirmation_note", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        return _blank_to_none(value)


class AmendConfirmedCaseRequest(BaseModel):
    """Amend confirmed details while preserving prior history in the timeline."""

    platform: PlatformName | None = None
    jurisdiction_state: str | None = None
    summary_title: str | None = None
    details: PaymentDiscrepancyDetails
    amendment_reason: str = Field(min_length=3, max_length=300)

    @field_validator("jurisdiction_state", "summary_title", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        return _blank_to_none(value)


class TimelineMilestoneRequest(BaseModel):
    """Append a worker-recorded milestone or status event to the case timeline."""

    event_type: TimelineMilestoneType
    title: str = Field(min_length=2, max_length=200)
    notes: str | None = None
    external_reference_id: str | None = None
    routing_channel_id: str | None = None
    linked_evidence_ids: list[str] | None = None
    occurred_at: datetime | None = None

    @field_validator("notes", "external_reference_id", "routing_channel_id", mode="before")
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        """Normalize empty strings to null."""
        return _blank_to_none(value)

    @field_validator("linked_evidence_ids", mode="before")
    @classmethod
    def clean_evidence_ids(cls, value: list[str] | None) -> list[str] | None:
        """Normalize empty lists to null."""
        if value is None:
            return None
        cleaned = [item.strip() for item in value if isinstance(item, str) and item.strip()]
        return cleaned if cleaned else None
