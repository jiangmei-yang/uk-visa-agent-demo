"""Persist distinct sponsor observations without reusing a legacy evidence waiver."""

from visa_agent.domain.models import Case, CaseStatus, InboundEvent, WorkflowStage
from visa_agent.domain.rules import advance_stage
from visa_agent.domain.sponsor_location import (
    LocationDimension,
    SponsorLocationStatement,
    parse_sponsor_location_statements,
)
from visa_agent.domain.sponsor_location_review import (
    current_sponsor_location_values,
    sponsor_location_binding,
)

LOCATION_REVIEW_REASON = ("Sponsor residence and current presence were recorded separately; "
                         "review UK-status evidence applicability before resuming preparation.")


def short_location_answer(body: str) -> bool | None:
    text = body.strip().rstrip("。.!！").strip().casefold()
    if text in {"yes", "yes, currently", "在", "在的", "是的", "是"}:
        return True
    if text in {"no", "not currently", "不在", "不在的", "不是", "不是的"}:
        return False
    return None


def pending_location_dimension(case: Case) -> LocationDimension | None:
    values = current_sponsor_location_values(case)
    if len(values["residence"]) == 1 and not values["current_presence"]:
        return "current_presence"
    if len(values["current_presence"]) == 1 and not values["residence"]:
        return "residence"
    return None


def record_sponsor_location(case: Case, event: InboundEvent, *,
                            verified_dimension: LocationDimension | None = None,
                            verified_question_event_id: str | None = None) -> bool:
    """Run only inside the consented, owner/order-checked inbound workflow.

    Pending explicit applicability/review support, new source observations must
    not inherit a historical boolean's authority to waive UK-status evidence.
    """
    if case.profile.funding_source != "personal_sponsor":
        return False
    statements = parse_sponsor_location_statements(event.body, source_event_id=event.id,
        sponsor_name=case.profile.sponsor_name, sponsor_relationship=case.profile.sponsor_relationship)
    short = short_location_answer(event.body)
    if (not statements and short is not None and verified_dimension is not None
            and verified_question_event_id
            and verified_dimension == pending_location_dimension(case)
            and case.sponsor_location_question_binding == sponsor_location_binding(case)):
        statements = [SponsorLocationStatement(dimension=verified_dimension, value=short,
            source_event_id=event.id, source_excerpt=event.body.strip(),
            context_question_event_id=verified_question_event_id,
            sponsor_name=case.profile.sponsor_name, sponsor_relationship=case.profile.sponsor_relationship)]
    statements = [item.model_copy(update={"identity_epoch": case.sponsor_location_epoch}) for item in statements]
    new = [item for item in statements if item not in case.sponsor_location_statements]
    if not new:
        return False
    case.sponsor_location_statements.extend(new)
    case.profile.sponsor_is_in_uk = None
    for evidence in case.active_evidence("sponsor_is_in_uk"):
        evidence.superseded = True
    case.latest_received_facts.pop("sponsor_is_in_uk", None)
    case.latest_changes.pop("sponsor_is_in_uk", None)
    case.profile_confirmed = False
    case.final_summary_confirmed = False
    values = current_sponsor_location_values(case)
    if any(not items for items in values.values()) and not any(len(items) > 1 for items in values.values()):
        # Incomplete information is ordinary intake, not an exceptional risk.
        # The independent applicability gate still blocks final delivery.
        return True
    case.status = CaseStatus.HUMAN_REVIEW_REQUIRED
    advance_stage(case, WorkflowStage.HUMAN_REVIEW_REQUIRED)
    if LOCATION_REVIEW_REASON not in (case.human_review_reason or ""):
        case.human_review_reason = "; ".join(filter(None, [case.human_review_reason, LOCATION_REVIEW_REASON]))
    return True
