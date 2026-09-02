from __future__ import annotations

from datetime import UTC, date, datetime

from visa_agent.domain.models import (
    Case,
    CaseStatus,
    GateResult,
    Issue,
    IssueSeverity,
    IssueStatus,
    Requirement,
    WorkflowStage,
)
from visa_agent.domain.policy import Policy

CRITICAL_FACTS = {
    "full_name",
    "date_of_birth",
    "planned_arrival_date",
    "planned_departure_date",
    "visit_purpose",
    "occupation_status",
    "funding_source",
}

KIND_TO_REQUIREMENT = {
    "passport": "passport",
    "student_letter": "status_evidence",
    "employment_letter": "status_evidence",
    "self_employment_evidence": "status_evidence",
    "conference_invitation": "purpose_evidence",
    "invitation_letter": "purpose_evidence",
    "funding_letter": "funding_evidence",
    "bank_statement": "funding_evidence",
    "sponsor_evidence": "sponsor_evidence",
    "certified_translation": "certified_translation",
    "legal_residence_evidence": "legal_residence",
}


def calculate_age(born: date, on_date: date) -> int:
    return on_date.year - born.year - ((on_date.month, on_date.day) < (born.month, born.day))


def route_in_scope(case: Case, policy: Policy, today: date) -> tuple[bool, str | None]:
    profile = case.profile
    if profile.date_of_birth is None:
        return False, "Date of birth has not been confirmed."
    if calculate_age(profile.date_of_birth, today) < int(policy.scope["minimum_age"]):
        return False, "Applicants under 18 require human review."
    if not profile.route_confirmed_standard_visitor:
        return False, "The applicant has not confirmed the Standard Visitor route."
    if profile.visit_purpose not in policy.scope["purposes"]:
        return False, "The visit purpose is outside this demo's supported scope."
    if profile.occupation_status not in policy.scope["occupations"]:
        return False, "The occupation type is outside this demo's supported scope."
    if profile.funding_source not in policy.scope["funding"]:
        return False, "The funding arrangement is outside this demo's supported scope."
    if profile.has_serious_history:
        return False, "Serious immigration, criminal, or refusal history requires human review."
    return True, None


def build_requirements(case: Case, policy: Policy) -> list[Requirement]:
    has_non_english = any(doc.language not in {"en", "cy"} for doc in case.documents)
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
                and case.profile.application_country != case.profile.nationality_country
            )
        matching = [
            doc.id
            for doc in case.documents
            if KIND_TO_REQUIREMENT.get(doc.kind) == rule.id
            and doc.status.value == "ACCEPTED_FOR_REVIEW"
        ]
        result.append(
            Requirement(
                id=rule.id,
                title=rule.title,
                blocker=rule.blocker,
                applicable=applicable,
                satisfied=(not applicable) or bool(matching),
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

    has_non_english = any(
        doc.language not in {"en", "cy"} and doc.status.value != "SUPERSEDED"
        for doc in case.documents
    )
    has_translation = any(
        doc.kind == "certified_translation" and doc.status.value == "ACCEPTED_FOR_REVIEW"
        for doc in case.documents
    )
    if has_non_english and not has_translation:
        _upsert_issue(
            case,
            Issue(
                id=f"issue-{case.id}-translation",
                code="MISSING_CERTIFIED_TRANSLATION",
                title="Certified translation required",
                detail="A non-English/Welsh document has no complete certified translation.",
                severity=IssueSeverity.BLOCKER,
            ),
        )
    elif has_translation:
        resolve_issue(case, "MISSING_CERTIFIED_TRANSLATION", "Certified translation received.")


def evaluate_gate(case: Case, policy: Policy, today: date) -> GateResult:
    in_scope, scope_reason = route_in_scope(case, policy, today)
    case.requirements = build_requirements(case, policy)
    critical_with_provenance = all(case.active_evidence(key) for key in CRITICAL_FACTS)
    checks = {
        "route_in_scope": in_scope,
        "applicant_age_at_least_18": bool(
            case.profile.date_of_birth and calculate_age(case.profile.date_of_birth, today) >= 18
        ),
        "profile_confirmed": case.profile_confirmed,
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
