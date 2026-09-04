from __future__ import annotations

import calendar
from datetime import UTC, date, datetime

from visa_agent.domain.locations import location_key
from visa_agent.domain.models import (
    Case,
    CaseStatus,
    DocumentStatus,
    Evidence,
    GateResult,
    Issue,
    IssueSeverity,
    IssueStatus,
    Requirement,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy

BASE_REQUIRED_FACTS = {
    "full_name",
    "date_of_birth",
    "nationality_country",
    "application_country",
    "planned_arrival_date",
    "planned_departure_date",
    "visit_purpose",
    "uk_accommodation",
    "estimated_trip_cost_gbp",
    "current_address",
    "occupation_status",
    "funding_source",
    "has_serious_history",
    "route_confirmed_standard_visitor",
}

CONDITIONAL_CRITICAL_FACTS = {
    "annual_income_gbp",
    "sponsor_name",
    "sponsor_relationship",
    "sponsor_is_in_uk",
}

# The evaluator treats every field that can become delivery-critical as critical.
CRITICAL_FACTS = BASE_REQUIRED_FACTS | CONDITIONAL_CRITICAL_FACTS


def calculate_age(born: date, on_date: date) -> int:
    return on_date.year - born.year - ((on_date.month, on_date.day) < (born.month, born.day))


def required_profile_facts(case: Case) -> set[str]:
    """Return the facts that must be explicit and traceable before this demo can deliver."""

    required = set(BASE_REQUIRED_FACTS)
    if case.profile.occupation_status in {"employed", "self_employed"}:
        required.add("annual_income_gbp")
    if case.profile.funding_source == "personal_sponsor":
        required.update({"sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"})
    return required


def _add_calendar_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def travel_dates_valid(case: Case, today: date) -> bool:
    arrival = case.profile.planned_arrival_date
    departure = case.profile.planned_departure_date
    return bool(
        arrival
        and departure
        and arrival >= today
        and departure > arrival
        and departure <= _add_calendar_months(arrival, 6)
    )


def route_in_scope(case: Case, policy: Policy, today: date) -> tuple[bool, str | None]:
    profile = case.profile
    if profile.date_of_birth is None:
        return False, "Date of birth has not been confirmed."
    if calculate_age(profile.date_of_birth, today) < int(policy.scope["minimum_age"]):
        return False, "Applicants under 18 require human review."
    if profile.route_confirmed_standard_visitor is not True:
        return False, "The applicant has not confirmed the Standard Visitor route."
    nationality = " ".join(
        item for item in (profile.nationality, profile.nationality_country) if item
    ).casefold()
    if "british" in nationality or nationality in {"uk", "united kingdom"}:
        return False, "British citizenship or UK right-of-abode status requires human review."
    if profile.visit_purpose not in policy.scope["purposes"]:
        return False, "The visit purpose is outside this demo's supported scope."
    if profile.occupation_status not in policy.scope["occupations"]:
        return False, "The occupation type is outside this demo's supported scope."
    if profile.funding_source not in policy.scope["funding"]:
        return False, "The funding arrangement is outside this demo's supported scope."
    if profile.has_serious_history is None:
        return False, "Criminal, civil, refusal, and immigration history has not been confirmed."
    if profile.has_serious_history is True:
        return False, "Serious immigration, criminal, or refusal history requires human review."
    return True, None


def build_requirements(case: Case, policy: Policy) -> list[Requirement]:
    non_english_documents = [
        doc
        for doc in case.documents
        if doc.language not in {"en", "cy"}
        and doc.kind != "unknown"
        and doc.status != DocumentStatus.SUPERSEDED
    ]
    has_non_english = bool(non_english_documents)
    result: list[Requirement] = []
    for rule in policy.requirements:
        applicable = rule.applies_when == "always"
        if rule.applies_when == "funding_personal_sponsor":
            applicable = case.profile.funding_source == "personal_sponsor"
        if rule.applies_when == "non_english_document":
            applicable = has_non_english
        if rule.applies_when == "application_country_differs_from_nationality_country":
            applicable = bool(
                case.profile.application_country
                and case.profile.nationality_country
                and location_key(case.profile.application_country) != location_key(case.profile.nationality_country)
            )
        matching = [
            doc.id
            for doc in case.documents
            if doc.kind in rule.acceptable_evidence
            and doc.status.value == "ACCEPTED_FOR_REVIEW"
        ]
        requirement_satisfied = bool(matching)
        if rule.id == "sponsor_evidence" and applicable:
            required_sponsor_kinds = {
                "sponsor_letter",
                "sponsor_funds",
                "relationship_evidence",
            }
            if case.profile.sponsor_is_in_uk is True:
                required_sponsor_kinds.add("sponsor_uk_status")
            present_sponsor_kinds = {
                doc.kind
                for doc in case.documents
                if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
            }
            requirement_satisfied = required_sponsor_kinds <= present_sponsor_kinds
        if rule.id == "certified_translation" and applicable:
            covered_document_ids = {
                doc.translation_for_document_id
                for doc in case.documents
                if doc.kind == "certified_translation"
                and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
                and doc.translation_for_document_id
            }
            matching = [
                doc.id
                for doc in case.documents
                if doc.kind == "certified_translation"
                and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
                and doc.translation_for_document_id in {item.id for item in non_english_documents}
            ]
            requirement_satisfied = all(
                item.id in covered_document_ids for item in non_english_documents
            )
        result.append(
            Requirement(
                id=rule.id,
                title=rule.title,
                blocker=rule.blocker,
                applicable=applicable,
                satisfied=(not applicable) or requirement_satisfied,
                document_ids=matching,
                rule_version=policy.version,
                source_urls=policy.sources,
            )
        )
    return result


def _upsert_issue(case: Case, issue: Issue) -> None:
    existing = next((item for item in case.issues if item.code == issue.code), None)
    if existing is None:
        case.issues.append(issue)
    elif existing.status == IssueStatus.RESOLVED:
        existing.status = IssueStatus.OPEN
        existing.detail = issue.detail
        existing.resolved_at = None
        existing.resolution = None


def resolve_issue(case: Case, code: str, resolution: str) -> None:
    for issue in case.issues:
        if issue.code == code and issue.status == IssueStatus.OPEN:
            issue.status = IssueStatus.RESOLVED
            issue.resolution = resolution
            issue.resolved_at = datetime.now(UTC)


def run_consistency_checks(case: Case) -> None:
    profile = case.profile
    passports = [doc for doc in case.documents if doc.kind in {"passport", "travel_document"} and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW]
    if passports and profile.planned_departure_date:
        if not passport_valid_through_stay(case):
            _upsert_issue(case, Issue(
                id=f"issue-{case.id}-passport-validity", code="PASSPORT_VALIDITY",
                title="Passport validity needs checking",
                detail="A readable expiry date on the travel document must cover the planned departure date. Please provide the expiry page or a replacement travel document.",
                severity=IssueSeverity.BLOCKER, related_document_ids=[doc.id for doc in passports],
            ))
        else:
            resolve_issue(case, "PASSPORT_VALIDITY", "The evidenced expiry date covers the planned stay.")
    invitation_end = case.active_evidence("invitation_event_end_date")
    if invitation_end and profile.planned_departure_date:
        event_end = date.fromisoformat(str(invitation_end[-1].value))
        if event_end > profile.planned_departure_date:
            _upsert_issue(
                case,
                Issue(
                    id=f"issue-{case.id}-date-conflict",
                    code="DATE_CONFLICT",
                    title="Travel and invitation dates differ",
                    detail=(
                        f"The current trip ends on {profile.planned_departure_date.isoformat()}, "
                        f"but the invitation states the event ends on {event_end.isoformat()}."
                    ),
                    severity=IssueSeverity.BLOCKER,
                ),
            )
        else:
            resolve_issue(case, "DATE_CONFLICT", "A replacement invitation now fits the trip dates.")

    non_english_documents = [
        doc
        for doc in case.documents
        if doc.language not in {"en", "cy"}
        and doc.kind != "unknown"
        and doc.status != DocumentStatus.SUPERSEDED
    ]
    translated_document_ids = {
        doc.translation_for_document_id
        for doc in case.documents
        if doc.kind == "certified_translation"
        and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW
        and doc.translation_for_document_id
    }
    untranslated_documents = [
        doc for doc in non_english_documents if doc.id not in translated_document_ids
    ]
    if untranslated_documents:
        filenames = ", ".join(item.filename for item in untranslated_documents)
        _upsert_issue(
            case,
            Issue(
                id=f"issue-{case.id}-translation",
                code="MISSING_CERTIFIED_TRANSLATION",
                title="Certified translation required",
                detail=f"These non-English/Welsh documents need linked translations: {filenames}.",
                severity=IssueSeverity.BLOCKER,
            ),
        )
    elif non_english_documents:
        resolve_issue(case, "MISSING_CERTIFIED_TRANSLATION", "Certified translation received.")

    evidence_by_fact: dict[str, list[Evidence]] = {}
    for evidence in case.evidence:
        if not evidence.superseded:
            evidence_by_fact.setdefault(evidence.fact_key, []).append(evidence)
    for fact_key, evidence_items in evidence_by_fact.items():
        values = {str(item.value).strip().casefold() for item in evidence_items}
        issue_code = f"EVIDENCE_CONFLICT_{fact_key.upper()}"
        if len(values) > 1:
            _upsert_issue(
                case,
                Issue(
                    id=f"issue-{case.id}-evidence-conflict-{fact_key}",
                    code=issue_code,
                    title=f"Conflicting evidence for {fact_key.replace('_', ' ')}",
                    detail=(
                        f"Active sources contain {len(values)} different values for {fact_key}; "
                        "a human must select or request the correct source."
                    ),
                    severity=IssueSeverity.BLOCKER,
                    related_document_ids=[
                        item.source_document_id
                        for item in evidence_items
                        if item.source_document_id is not None
                    ],
                ),
            )
        else:
            resolve_issue(case, issue_code, "Only one active value remains across the evidence.")


def passport_valid_through_stay(case: Case) -> bool:
    if case.profile.planned_departure_date is None:
        return False
    passport_ids = {doc.id for doc in case.documents if doc.kind in {"passport", "travel_document"} and doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW}
    for evidence in case.active_evidence("passport_expiry_date"):
        if evidence.source_document_id not in passport_ids:
            continue
        try:
            if date.fromisoformat(str(evidence.value)) >= case.profile.planned_departure_date:
                return True
        except ValueError:
            continue
    return False


def evaluate_gate(case: Case, policy: Policy, today: date) -> GateResult:
    in_scope, scope_reason = route_in_scope(case, policy, today)
    case.requirements = build_requirements(case, policy)
    required_facts = required_profile_facts(case)
    complete_profile = all(getattr(case.profile, key) is not None for key in required_facts)
    critical_with_provenance = all(case.active_evidence(key) for key in required_facts)
    checks = {
        "route_in_scope": in_scope,
        "applicant_age_at_least_18": bool(
            case.profile.date_of_birth and calculate_age(case.profile.date_of_birth, today) >= 18
        ),
        "profile_confirmed": case.profile_confirmed,
        "required_profile_facts_complete": complete_profile,
        "travel_dates_are_valid_and_within_six_months": travel_dates_valid(case, today),
        "passport_valid_through_stay": passport_valid_through_stay(case),
        "all_blocker_requirements_resolved": all(
            item.satisfied for item in case.requirements if item.applicable and item.blocker
        ),
        "no_unresolved_blocker_issue": not case.open_blockers(),
        "every_critical_fact_has_provenance": critical_with_provenance,
        "applicant_explicitly_confirmed_final_summary": case.final_summary_confirmed,
        "policy_snapshot_is_current": policy.is_current(today),
    }
    reasons = [label.replace("_", " ") for label, passed in checks.items() if not passed]
    if scope_reason and not in_scope:
        reasons.insert(0, scope_reason)
    return GateResult(allowed=all(checks.values()), checks=checks, reasons=reasons)


ALLOWED_TRANSITIONS: dict[CaseStatus, set[CaseStatus]] = {
    CaseStatus.DRAFT: {CaseStatus.READY_FOR_HUMAN_REVIEW, CaseStatus.HUMAN_REVIEW_REQUIRED},
    CaseStatus.READY_FOR_HUMAN_REVIEW: {CaseStatus.DELIVERED_AFTER_CONFIRMATION},
    CaseStatus.DELIVERED_AFTER_CONFIRMATION: set(),
    CaseStatus.HUMAN_REVIEW_REQUIRED: set(),
}


def transition(case: Case, target: CaseStatus) -> None:
    if target not in ALLOWED_TRANSITIONS[case.status]:
        raise ValueError(f"Transition {case.status} -> {target} is not allowed")
    case.status = target


STAGE_ORDER = [
    WorkflowStage.NEW,
    WorkflowStage.CONSENTED,
    WorkflowStage.ROUTE_SCREENING,
    WorkflowStage.INTAKE,
    WorkflowStage.PROFILE_CONFIRMATION,
    WorkflowStage.COLLECTING_DOCUMENTS,
    WorkflowStage.DOCUMENT_REVIEW,
    WorkflowStage.FINAL_CONFIRMATION,
    WorkflowStage.READY_FOR_HUMAN_REVIEW,
    WorkflowStage.DELIVERED_AFTER_CONFIRMATION,
]

STAGE_TRANSITIONS: dict[WorkflowStage, set[WorkflowStage]] = {
    stage: ({STAGE_ORDER[index + 1], WorkflowStage.HUMAN_REVIEW_REQUIRED}
            if index + 1 < len(STAGE_ORDER)
            else {WorkflowStage.HUMAN_REVIEW_REQUIRED})
    for index, stage in enumerate(STAGE_ORDER)
}
STAGE_TRANSITIONS[WorkflowStage.HUMAN_REVIEW_REQUIRED] = set()


def advance_stage(case: Case, target: WorkflowStage) -> None:
    if target == case.stage:
        return
    if target == WorkflowStage.HUMAN_REVIEW_REQUIRED:
        if target not in STAGE_TRANSITIONS[case.stage]:
            raise ValueError(f"Stage transition {case.stage} -> {target} is not allowed")
        case.stage = target
        return
    current_index = STAGE_ORDER.index(case.stage)
    target_index = STAGE_ORDER.index(target)
    if target_index < current_index:
        raise ValueError(f"Stage transition {case.stage} -> {target} is not allowed")
    while case.stage != target:
        next_stage = STAGE_ORDER[STAGE_ORDER.index(case.stage) + 1]
        if next_stage not in STAGE_TRANSITIONS[case.stage]:
            raise ValueError(f"Stage transition {case.stage} -> {next_stage} is not allowed")
        case.stage = next_stage
