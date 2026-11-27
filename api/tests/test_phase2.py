"""Phase 2 tests: payment cases, null unknowns, confirmation gate, evidence, timeline, routing."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.cases import service
from app.cases.validation import validate_field_provenance
from app.core.database import Base, ensure_default_worker
from app.models.case import CaseTimelineEvent, EvidenceItem, GrievanceCase, RoutingChannel  # noqa: F401
from app.models.worker import WorkerProfile  # noqa: F401
from app.routing.matcher import ensure_routing_seed, match_verified_routes
from app.schemas.case import (
    AmendConfirmedCaseRequest,
    ConfirmCaseRequest,
    CreateCaseRequest,
    FieldProvenance,
    PaymentDiscrepancyDetails,
    TimelineMilestoneRequest,
    UpdateDraftRequest,
)
from app.storage.local_store import FileValidationError, LocalStorageService

PNG_HEADER_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _make_session_factory(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_create_retrieve_and_null_unknowns_persist_across_sessions(tmp_path):
    """Created payment case persists across sessions and keeps blank/unknown fields null."""
    SessionFactory = _make_session_factory(tmp_path / "wallet.db")
    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        created = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="swiggy",
                summary_title="Weekend rain incentive missing",
                details=PaymentDiscrepancyDetails(
                    platform="swiggy",
                    expected_amount_inr=1200,
                    received_amount_inr=450,
                    work_city="   ",
                    issue_description="Completed 18 rain orders on Sunday but incentive was not credited.",
                ),
            ),
        )
        case_id = created.id
        assert created.status == "draft"
        assert created.confirmed_details is None
        assert created.draft_details["work_city"] is None
        assert created.draft_details["incentive_scheme_name"] is None
        assert created.draft_details["discrepancy_amount_inr"] is None

    with SessionFactory() as fresh_db:
        reloaded = service.get_case_or_none(fresh_db, case_id=case_id, worker_id=worker_id)
        assert reloaded is not None
        serialized = service.serialize_case(fresh_db, reloaded)
        assert serialized["status"] == "draft"
        assert serialized["confirmed_details"] is None
        assert serialized["draft_details"]["expected_amount_inr"] == 1200
        assert serialized["draft_details"]["prior_ticket_id"] is None
        assert len(serialized["missing_fields"]) > 0
        assert len(serialized["timeline"]) == 1
        assert serialized["timeline"][0]["event_type"] == "case_created"
        assert serialized["timeline"][0]["event_source"] == "system"


def test_other_categories_rejected_and_provenance_enforced(tmp_path):
    """Non-payment categories are blocked in Phase 2 and ungrounded quotes are rejected."""
    SessionFactory = _make_session_factory(tmp_path / "wallet.db")
    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        with pytest.raises(ValueError, match="payment_discrepancy"):
            service.create_payment_case(
                db,
                worker_id=worker_id,
                payload=CreateCaseRequest(category="account_suspension", platform="uber"),
            )

    with pytest.raises(ValueError, match="exact excerpt"):
        validate_field_provenance(
            {
                "expected_amount_inr": FieldProvenance(
                    origin="transcript_quote",
                    source_quote="I was promised 5000 rupees",
                )
            },
            transcript_context="Swiggy paid me 450 instead of 1200 for Sunday.",
        )


def test_mandatory_confirmation_and_append_only_timeline_semantics(tmp_path):
    """Distinguishes system vs worker events, submission vs evidence-backed submission vs acknowledgement."""
    SessionFactory = _make_session_factory(tmp_path / "wallet.db")
    storage = LocalStorageService(str(tmp_path / "evidence"), max_upload_mb=10)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="uber",
                details=PaymentDiscrepancyDetails(
                    platform="uber",
                    issue_description="Weekly quest bonus missing",
                ),
            ),
        )

        with pytest.raises(ValueError, match="confirm=true"):
            service.confirm_case(
                db,
                case=case,
                payload=ConfirmCaseRequest(
                    confirm=False,
                    details=PaymentDiscrepancyDetails(
                        platform="uber",
                        issue_description="Weekly quest bonus missing",
                    ),
                ),
            )
        assert case.status == "draft"
        assert case.confirmed_details is None

        service.update_draft_case(
            db,
            case=case,
            payload=UpdateDraftRequest(
                details=PaymentDiscrepancyDetails(
                    platform="uber",
                    expected_amount_inr=1500,
                    issue_description="Weekly quest bonus missing for 25 rides",
                )
            ),
        )
        service.confirm_case(
            db,
            case=case,
            payload=ConfirmCaseRequest(
                confirm=True,
                details=PaymentDiscrepancyDetails(
                    platform="uber",
                    expected_amount_inr=1500,
                    received_amount_inr=0,
                    issue_description="Weekly quest bonus missing for 25 rides",
                ),
                worker_confirmation_note="Reviewed numbers against Uber Driver app.",
            ),
        )
        assert case.status == "confirmed"

        service.amend_confirmed_case(
            db,
            case=case,
            payload=AmendConfirmedCaseRequest(
                details=PaymentDiscrepancyDetails(
                    platform="uber",
                    expected_amount_inr=1500,
                    received_amount_inr=0,
                    prior_ticket_id="UB-9921",
                    issue_description="Weekly quest bonus missing for 25 rides",
                ),
                amendment_reason="Added support ticket number after checking Help inbox.",
            ),
        )

        # Worker-reported submission does NOT imply platform acknowledgement
        service.append_milestone(
            db,
            case=case,
            payload=TimelineMilestoneRequest(
                event_type="worker_reported_submission",
                title="Reported raising ticket in app",
                external_reference_id="UB-9921",
            ),
        )
        assert case.status == "submission_reported"

        # Evidence-backed submission MUST reject missing/unknown evidence links
        with pytest.raises(ValueError, match="requires at least one uploaded evidence item"):
            service.append_milestone(
                db,
                case=case,
                payload=TimelineMilestoneRequest(
                    event_type="evidence_backed_submission",
                    title="Submission without proof",
                ),
            )

        with pytest.raises(ValueError, match="must belong to uploaded evidence items"):
            service.append_milestone(
                db,
                case=case,
                payload=TimelineMilestoneRequest(
                    event_type="evidence_backed_submission",
                    title="Submission with bogus ID",
                    linked_evidence_ids=["nonexistent-id"],
                ),
            )

        # Upload ticket screenshot and link it to evidence-backed submission
        ticket_ev = service.add_evidence_item(
            db,
            case=case,
            storage=storage,
            evidence_category="support_communication",
            raw_bytes=PNG_HEADER_BYTES,
            original_filename="ticket_screen.png",
            declared_content_type="image/png",
            worker_note="Help ticket confirmation screenshot",
        )
        db.refresh(case)

        service.append_milestone(
            db,
            case=case,
            payload=TimelineMilestoneRequest(
                event_type="evidence_backed_submission",
                title="Submitted complaint with ticket screenshot",
                external_reference_id="UB-9921",
                linked_evidence_ids=[ticket_ev.id],
            ),
        )
        assert case.status == "submission_with_evidence"

        # Platform acknowledgement reported separately
        service.append_milestone(
            db,
            case=case,
            payload=TimelineMilestoneRequest(
                event_type="platform_acknowledgement_reported",
                title="Worker logged email acknowledgement from support",
                external_reference_id="UB-ACK-77",
            ),
        )
        assert case.status == "acknowledgement_reported"

        db.refresh(case)
        events = [(ev.event_type, ev.event_source) for ev in case.timeline_events]
        assert events == [
            ("case_created", "system"),
            ("draft_updated", "system"),
            ("case_confirmed", "system"),
            ("details_amended", "system"),
            ("worker_reported_submission", "worker"),
            ("evidence_added", "system"),
            ("evidence_backed_submission", "worker"),
            ("platform_acknowledgement_reported", "worker"),
        ]
        assert case.timeline_events[4].snapshot_payload["platform_receipt_confirmed"] is False
        assert case.timeline_events[6].linked_evidence_ids == [ticket_ev.id]


def test_evidence_checklist_updates_and_rejects_spoofed_files(tmp_path):
    """Checklist marks items uploaded only when real validated files are persisted."""
    SessionFactory = _make_session_factory(tmp_path / "wallet.db")
    storage = LocalStorageService(str(tmp_path / "evidence"), max_upload_mb=10)

    with SessionFactory() as db:
        worker_id = ensure_default_worker(db)
        ensure_routing_seed(db)
        case = service.create_payment_case(
            db,
            worker_id=worker_id,
            payload=CreateCaseRequest(
                platform="rapido",
                details=PaymentDiscrepancyDetails(
                    platform="rapido",
                    issue_description="Daily target payout short by Rs 300",
                ),
            ),
        )

        initial = service.serialize_case(db, case)["evidence_checklist"]
        assert initial["essential_uploaded"] == 0
        assert initial["essential_missing"] == 2
        assert initial["helpful_missing"] == 2
        assert all(item["status"] == "missing" for item in initial["items"])

        with pytest.raises(FileValidationError, match="Unsupported file format"):
            service.add_evidence_item(
                db,
                case=case,
                storage=storage,
                evidence_category="payout_summary",
                raw_bytes=b"not a real png image",
                original_filename="fake.png",
                declared_content_type="image/png",
                worker_note=None,
            )

        service.add_evidence_item(
            db,
            case=case,
            storage=storage,
            evidence_category="payout_summary",
            raw_bytes=PNG_HEADER_BYTES,
            original_filename="payout.png",
            declared_content_type="image/png",
            worker_note="Weekly ledger screenshot",
        )
        db.refresh(case)
        updated = service.serialize_case(db, case)["evidence_checklist"]
        assert updated["essential_uploaded"] == 1
        assert updated["essential_missing"] == 1
        assert updated["ready_for_escalation"] is False

        by_key = {item["key"]: item for item in updated["items"]}
        assert by_key["payout_summary"]["status"] == "uploaded"
        assert by_key["payout_summary"]["priority"] == "essential"
        assert by_key["incentive_offer"]["status"] == "missing"
        assert by_key["order_or_trip_records"]["priority"] == "helpful"
        assert by_key["order_or_trip_records"]["status"] == "missing"


def test_verified_routing_audit_downgrades_unverified_platform_routes(tmp_path):
    """Unverified platform payment routes must return 'Route requires verification'; only official statutory route is verified."""
    SessionFactory = _make_session_factory(tmp_path / "wallet.db")
    with SessionFactory() as db:
        ensure_routing_seed(db)

        for platform in ("swiggy", "zomato", "rapido", "uber", "other"):
            route = match_verified_routes(db, platform=platform, category="payment_discrepancy")
            assert route["route_status"] == "requires_verification"
            assert route["status_message"] == "Route requires verification"
            assert route["platform_channels"] == []
            assert len(route["statutory_channels"]) == 1
            samadhan = route["statutory_channels"][0]
            assert samadhan["source_url"] == "https://samadhan.labour.gov.in"
            assert samadhan["verification_status"] == "verified"
            assert samadhan["what_source_confirms"]
            assert samadhan["last_verified_at"] == "2026-11-27"

        state_route = match_verified_routes(
            db,
            platform="swiggy",
            category="payment_discrepancy",
            jurisdiction_state="Karnataka",
        )
        assert state_route["route_status"] == "requires_verification"
        assert state_route["status_message"] == "Route requires verification"
        assert state_route["platform_channels"] == []

        other_cat = match_verified_routes(db, platform="uber", category="account_suspension")
        assert other_cat["route_status"] == "requires_verification"
        assert other_cat["status_message"] == "Route requires verification"
