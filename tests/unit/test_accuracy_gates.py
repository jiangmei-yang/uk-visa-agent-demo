from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from visa_agent.domain.models import Case, CaseProfile, Document, DocumentStatus, Evidence
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import (
    build_requirements,
    evaluate_gate,
    required_profile_facts,
    run_consistency_checks,
)

POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
TODAY = date(2026, 9, 2)


def case_with(**updates: object) -> Case:
    profile = CaseProfile(
        full_name="Lin Xiao",
        date_of_birth=date(1990, 3, 12),
        nationality="Chinese",
        nationality_country="China",
        application_country="Hong Kong",
        planned_arrival_date=date(2026, 11, 1),
        planned_departure_date=date(2026, 11, 8),
        visit_purpose="conference",
        uk_accommodation="Northstar Hotel, London",
        estimated_trip_cost_gbp=2200,
        current_address="88 Synthetic Road, Hong Kong",
        occupation_status="employed",
        annual_income_gbp=18000,
        funding_source="self",
        has_serious_history=False,
        route_confirmed_standard_visitor=True,
    )
    for field, value in updates.items():
        setattr(profile, field, value)
    case = Case(
        id="case-accuracy",
        external_thread_id="thread-accuracy",
        applicant_contact="applicant@example.test",
        policy_version=POLICY.version,
        profile=profile,
        profile_confirmed=True,
        final_summary_confirmed=True,
    )
    for index, field in enumerate(sorted(required_profile_facts(case))):
        value = getattr(profile, field)
        if value is None:
            continue
        case.evidence.append(
            Evidence(
                id=f"evidence-{index}",
                fact_key=field,
                value=value.isoformat() if isinstance(value, date) else value,
                source_event_id="event-accuracy",
                source_excerpt=f"confirmed {field}",
                extraction_method="test",
                model_version="none",
                confidence=1,
                confirmed=True,
                created_at=datetime(2026, 9, 2, 9, tzinfo=UTC),
            )
        )
    return case


def document(
    kind: str,
    *,
    language: str = "en",
    suffix: str = "",
    translation_for_document_id: str | None = None,
) -> Document:
    return Document(
        id=f"doc-{kind}{suffix}",
        filename=f"{kind}{suffix}.pdf",
        kind=kind,
        sha256=(kind * 64)[:64],
        mime_type="application/pdf",
        status=DocumentStatus.ACCEPTED_FOR_REVIEW,
        source_event_id="event-accuracy",
        path=f"/synthetic/{kind}.pdf",
        language=language,
        translation_for_document_id=translation_for_document_id,
    )


def test_silence_about_history_can_never_pass_scope_or_completeness() -> None:
    case = case_with(has_serious_history=None)

    gate = evaluate_gate(case, POLICY, TODAY)

    assert gate.checks["route_in_scope"] is False
    assert gate.checks["required_profile_facts_complete"] is False
    assert any("history has not been confirmed" in reason for reason in gate.reasons)


def test_british_citizenship_is_held_for_human_route_review() -> None:
    case = case_with(nationality="British", nationality_country="United Kingdom")

    gate = evaluate_gate(case, POLICY, TODAY)

    assert gate.checks["route_in_scope"] is False
    assert any("right-of-abode" in reason for reason in gate.reasons)


def test_invalid_or_over_six_month_itineraries_cannot_pass() -> None:
    reversed_case = case_with(
        planned_arrival_date=date(2026, 11, 8),
        planned_departure_date=date(2026, 11, 1),
    )
    long_case = case_with(
        planned_arrival_date=date(2026, 10, 1),
        planned_departure_date=date(2027, 4, 2),
    )

    assert evaluate_gate(reversed_case, POLICY, TODAY).checks[
        "travel_dates_are_valid_and_within_six_months"
    ] is False
    assert evaluate_gate(long_case, POLICY, TODAY).checks[
        "travel_dates_are_valid_and_within_six_months"
    ] is False


def test_employed_and_personal_sponsor_branches_require_conditional_facts() -> None:
    employed = case_with(annual_income_gbp=None)
    sponsored = case_with(
        occupation_status="student",
        annual_income_gbp=None,
        funding_source="personal_sponsor",
        sponsor_name=None,
        sponsor_relationship=None,
        sponsor_is_in_uk=None,
    )

    assert "annual_income_gbp" in required_profile_facts(employed)
    assert evaluate_gate(employed, POLICY, TODAY).checks["required_profile_facts_complete"] is False
    assert {"sponsor_name", "sponsor_relationship", "sponsor_is_in_uk"} <= required_profile_facts(
        sponsored
    )
    assert evaluate_gate(sponsored, POLICY, TODAY).checks[
        "required_profile_facts_complete"
    ] is False


def test_non_national_application_requires_legal_residence_evidence() -> None:
    case = case_with()
    case.documents = [
        document("passport"),
        document("employment_letter"),
        document("conference_invitation"),
        document("bank_statement"),
    ]

    requirements = {item.id: item for item in build_requirements(case, POLICY)}

    assert requirements["legal_residence"].applicable is True
    assert requirements["legal_residence"].satisfied is False
    case.documents.append(document("status_document"))
    requirements = {item.id: item for item in build_requirements(case, POLICY)}
    assert requirements["legal_residence"].satisfied is True


def test_sponsor_evidence_requires_each_official_component() -> None:
    case = case_with(
        occupation_status="student",
        funding_source="personal_sponsor",
        sponsor_name="Mei Chen",
        sponsor_relationship="mother",
        sponsor_is_in_uk=True,
    )
    case.documents = [
        document("sponsor_letter"),
        document("sponsor_funds"),
        document("relationship_evidence"),
    ]

    requirements = {item.id: item for item in build_requirements(case, POLICY)}
    assert requirements["sponsor_evidence"].satisfied is False

    case.documents.append(document("sponsor_uk_status"))
    requirements = {item.id: item for item in build_requirements(case, POLICY)}
    assert requirements["sponsor_evidence"].satisfied is True


def test_policy_and_prompt_canonical_values_align() -> None:
    assert "family_or_friends" in POLICY.scope["purposes"]
    assert "employer_or_school" in POLICY.scope["funding"]


def test_every_non_english_document_needs_its_own_linked_translation() -> None:
    case = case_with()
    first = document("other_supporting_document", language="zh", suffix="-one")
    second = document("other_supporting_document", language="fr", suffix="-two")
    first_translation = document(
        "certified_translation",
        suffix="-one",
        translation_for_document_id=first.id,
    )
    case.documents = [first, second, first_translation]

    run_consistency_checks(case)
    requirements = {item.id: item for item in build_requirements(case, POLICY)}

    assert requirements["certified_translation"].satisfied is False
    assert any(issue.code == "MISSING_CERTIFIED_TRANSLATION" for issue in case.open_blockers())

    case.documents.append(
        document(
            "certified_translation",
            suffix="-two",
            translation_for_document_id=second.id,
        )
    )
    run_consistency_checks(case)
    requirements = {item.id: item for item in build_requirements(case, POLICY)}
    assert requirements["certified_translation"].satisfied is True
    assert not any(issue.code == "MISSING_CERTIFIED_TRANSLATION" for issue in case.open_blockers())


def test_conflicting_active_evidence_is_a_blocker_until_one_source_is_superseded() -> None:
    case = case_with()
    case.evidence.extend(
        [
            Evidence(
                id="passport-name",
                fact_key="full_name",
                value="Lin Xiao",
                source_event_id="event-one",
                source_document_id="passport",
                source_excerpt="Lin Xiao",
                extraction_method="test",
                model_version="none",
                confidence=1,
            ),
            Evidence(
                id="letter-name",
                fact_key="full_name",
                value="Lin Xia",
                source_event_id="event-two",
                source_document_id="student-letter",
                source_excerpt="Lin Xia",
                extraction_method="test",
                model_version="none",
                confidence=1,
            ),
        ]
    )

    run_consistency_checks(case)
    assert any(issue.code == "EVIDENCE_CONFLICT_FULL_NAME" for issue in case.open_blockers())

    next(item for item in case.evidence if item.id == "letter-name").superseded = True
    run_consistency_checks(case)
    assert not any(issue.code == "EVIDENCE_CONFLICT_FULL_NAME" for issue in case.open_blockers())
