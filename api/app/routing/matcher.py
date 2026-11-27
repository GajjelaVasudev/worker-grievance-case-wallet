"""Curated grievance routing seeder and deterministic matcher."""

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.case import RoutingChannel

_SEED_FILE = Path(__file__).resolve().parent / "data" / "verified_routes.json"

TIER_ORDER = {
    "tier_1_in_app": 1,
    "tier_2_grievance_officer": 2,
    "tier_3_statutory": 3,
}


def ensure_routing_seed(db: Session) -> int:
    """Idempotently load verified routing channels from versioned JSON."""
    raw_entries = json.loads(_SEED_FILE.read_text(encoding="utf-8"))
    count = 0
    for entry in raw_entries:
        existing = db.scalar(select(RoutingChannel).where(RoutingChannel.id == entry["id"]))
        if existing is None:
            db.add(RoutingChannel(**entry))
        else:
            for key, value in entry.items():
                setattr(existing, key, value)
        count += 1
    db.commit()
    return count


def _serialize_channel(channel: RoutingChannel) -> dict:
    return {
        "id": channel.id,
        "platform": channel.platform,
        "category": channel.category,
        "jurisdiction": channel.jurisdiction,
        "tier": channel.tier,
        "channel_name": channel.channel_name,
        "contact_type": channel.contact_type,
        "contact_value": channel.contact_value,
        "instructions": channel.instructions,
        "expected_sla_days": channel.expected_sla_days,
        "verification_status": channel.verification_status,
        "source_url": channel.source_url,
        "what_source_confirms": channel.what_source_confirms,
        "last_verified_at": channel.last_verified_at,
        "disclaimer_text": channel.disclaimer_text,
    }


def match_verified_routes(
    db: Session,
    *,
    platform: str,
    category: str,
    jurisdiction_state: str | None = None,
) -> dict:
    """Match strictly verified channels; never invent routes for unsupported combinations."""
    normalized_platform = (platform or "other").strip().lower()
    normalized_category = (category or "").strip().lower()
    requested_jurisdiction = (jurisdiction_state or "national").strip().lower()

    if normalized_category != "payment_discrepancy":
        return {
            "route_status": "requires_verification",
            "status_message": "Route requires verification",
            "reason": f"No verified routing registry is currently enabled for category '{category}'.",
            "requested": {
                "platform": normalized_platform,
                "category": normalized_category,
                "jurisdiction": requested_jurisdiction,
            },
            "platform_channels": [],
            "statutory_channels": [],
        }

    # Check exact platform + category + requested jurisdiction (or national fallback for national requests).
    platform_rows = list(
        db.scalars(
            select(RoutingChannel).where(
                RoutingChannel.platform == normalized_platform,
                RoutingChannel.category == normalized_category,
                RoutingChannel.verification_status == "verified",
            )
        )
    )

    if requested_jurisdiction != "national":
        state_specific = [row for row in platform_rows if row.jurisdiction.lower() == requested_jurisdiction]
        platform_rows = state_specific

    platform_rows.sort(key=lambda row: TIER_ORDER.get(row.tier, 99))

    statutory_rows = []
    if requested_jurisdiction == "national":
        statutory_rows = list(
            db.scalars(
                select(RoutingChannel).where(
                    RoutingChannel.platform == "statutory",
                    RoutingChannel.category == normalized_category,
                    RoutingChannel.jurisdiction == "national",
                    RoutingChannel.verification_status == "verified",
                )
            )
        )

    if not platform_rows:
        return {
            "route_status": "requires_verification",
            "status_message": "Route requires verification",
            "reason": (
                f"No verified platform route is recorded for platform '{normalized_platform}', "
                f"category '{normalized_category}', jurisdiction '{requested_jurisdiction}'."
            ),
            "requested": {
                "platform": normalized_platform,
                "category": normalized_category,
                "jurisdiction": requested_jurisdiction,
            },
            "platform_channels": [],
            "statutory_channels": [_serialize_channel(row) for row in statutory_rows],
        }

    return {
        "route_status": "verified",
        "status_message": f"Found {len(platform_rows)} verified platform channel(s)",
        "reason": None,
        "requested": {
            "platform": normalized_platform,
            "category": normalized_category,
            "jurisdiction": requested_jurisdiction,
        },
        "platform_channels": [_serialize_channel(row) for row in platform_rows],
        "statutory_channels": [_serialize_channel(row) for row in statutory_rows],
    }
