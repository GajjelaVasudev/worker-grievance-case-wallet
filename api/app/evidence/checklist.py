"""Category-specific evidence checklist evaluation."""

from dataclasses import dataclass

from app.models.case import EvidenceItem


@dataclass(frozen=True)
class ChecklistRequirement:
    """Definition of a single category-specific evidence requirement."""

    key: str
    label: str
    priority: str  # "essential" | "helpful"
    guidance: str


PAYMENT_DISCREPANCY_CHECKLIST: tuple[ChecklistRequirement, ...] = (
    ChecklistRequirement(
        key="payout_summary",
        label="Payout summary",
        priority="essential",
        guidance="Screenshot or PDF of your weekly/daily payout statement showing the amount credited.",
    ),
    ChecklistRequirement(
        key="incentive_offer",
        label="Incentive scheme / offer details",
        priority="essential",
        guidance="Screenshot of the target, peak-pay, or minimum-guarantee offer showing the promised payout.",
    ),
    ChecklistRequirement(
        key="order_or_trip_records",
        label="Relevant order or trip records",
        priority="helpful",
        guidance="Trip/order history screenshots showing completed trips or orders during the payout cycle.",
    ),
    ChecklistRequirement(
        key="support_communication",
        label="Previous support communication",
        priority="helpful",
        guidance="Screenshot of prior in-app help tickets, chat replies, or email responses if already contacted.",
    ),
)


def evaluate_evidence_checklist(category: str, uploaded_items: list[EvidenceItem]) -> dict:
    """Evaluate evidence completeness strictly from persisted uploaded items."""
    if category != "payment_discrepancy":
        return {
            "category": category,
            "supported": False,
            "items": [],
            "essential_total": 0,
            "essential_uploaded": 0,
            "essential_missing": 0,
            "helpful_total": 0,
            "helpful_uploaded": 0,
            "helpful_missing": 0,
            "ready_for_escalation": False,
        }

    counts_by_category: dict[str, int] = {}
    for item in uploaded_items:
        counts_by_category[item.evidence_category] = counts_by_category.get(item.evidence_category, 0) + 1

    evaluated_items = []
    essential_total = 0
    essential_uploaded = 0
    helpful_total = 0
    helpful_uploaded = 0

    for req in PAYMENT_DISCREPANCY_CHECKLIST:
        count = counts_by_category.get(req.key, 0)
        is_uploaded = count > 0
        if req.priority == "essential":
            essential_total += 1
            if is_uploaded:
                essential_uploaded += 1
        else:
            helpful_total += 1
            if is_uploaded:
                helpful_uploaded += 1

        evaluated_items.append(
            {
                "key": req.key,
                "label": req.label,
                "priority": req.priority,
                "status": "uploaded" if is_uploaded else "missing",
                "uploaded_count": count,
                "guidance": req.guidance,
            }
        )

    return {
        "category": category,
        "supported": True,
        "items": evaluated_items,
        "essential_total": essential_total,
        "essential_uploaded": essential_uploaded,
        "essential_missing": essential_total - essential_uploaded,
        "helpful_total": helpful_total,
        "helpful_uploaded": helpful_uploaded,
        "helpful_missing": helpful_total - helpful_uploaded,
        "ready_for_escalation": essential_uploaded == essential_total,
    }
