"""Financial-review contracts from synthetic evidence; no sufficiency scoring."""

import pytest

from visa_agent.domain.financial_review import (
    apply_financial_consistency_checks,
    financial_observation_is_valid,
)
from visa_agent.domain.models import (
    Case,
    CaseProfile,
    Document,
    DocumentStatus,
    Evidence,
    IssueStatus,
    ProvenanceState,
)


def document(identifier, kind, status=DocumentStatus.ACCEPTED_FOR_REVIEW):
    return Document(id=identifier, filename=identifier + ".pdf", kind=kind, sha256="a" * 64,
        mime_type="application/pdf", status=status, source_event_id="source-" + identifier,
        path="/synthetic-not-read/" + identifier + ".pdf")


def observation(identifier, document_id, *, kind="closing_balance", subject="Sample Applicant",
                amount="1200.00", currency="GBP", period="closing", basis="unspecified",
                as_of="2026-08-31", account="ACCOUNT-01", superseded=False,
                state=ProvenanceState.EXTRACTED_UNVERIFIED, value=None):
    payload = ({"kind": kind, "subject_name": subject, "amount": amount, "currency": currency,
                "period": period, "basis": basis, "as_of": as_of, "account_reference": account}
               if value is None else value)
    if value is None:
        subject_label = "Account holder" if kind == "closing_balance" else "Employee"
        date_label = "Statement date" if kind == "closing_balance" else "Letter date"
        payload.update(subject_page=1, subject_excerpt=f"{subject_label}: {subject}",
                       date_page=1, date_excerpt=f"{date_label}: {as_of}",
                       account_page=1 if account else None,
                       account_excerpt=f"Account reference {account}" if account else None)
    amount_label = (
        "Closing balance"
        if kind == "closing_balance"
        else f"{basis.capitalize() + ' ' if basis != 'unspecified' else ''}{period} salary"
    )
    return Evidence(id=identifier, fact_key="financial_observation", value=payload,
        source_event_id="event-" + identifier, source_document_id=document_id,
        source_excerpt=f"{amount_label} {currency} {amount}", page=1,
        extraction_method="synthetic",
        model_version="synthetic", confidence=1, superseded=superseded, provenance_state=state)


def case(*documents, evidence=(), full_name="Sample Applicant", sponsor="Sample Sponsor"):
    return Case(id="synthetic-financial-case", external_thread_id="synthetic-thread",
        applicant_contact="synthetic@example.test", policy_version="synthetic",
        profile=CaseProfile(full_name=full_name, sponsor_name=sponsor),
        documents=list(documents), evidence=list(evidence))


def open_codes(item):
    return [issue.code for issue in item.issues if issue.status == IssueStatus.OPEN]


@pytest.mark.parametrize(("doc_kind", "obs_kind", "subject", "expected_field"), [
    ("bank_statement", "closing_balance", "Different Holder", "applicant"),
    ("employment_letter", "salary", "Different Employee", "applicant"),
    ("sponsor_funds", "closing_balance", "Different Sponsor", "sponsor"),
])
def test_known_owner_mismatch_is_a_stable_blocker(doc_kind, obs_kind, subject, expected_field):
    doc = document("owner-doc", doc_kind)
    period = "annual" if obs_kind == "salary" else "closing"
    item = case(doc, evidence=[observation("owner-observation", doc.id, kind=obs_kind,
        subject=subject, period=period, account=None if obs_kind == "salary" else "ACCOUNT-01")])
    before = (item.profile.model_dump(), [e.model_dump() for e in item.evidence],
              [d.model_dump() for d in item.documents])
    apply_financial_consistency_checks(item)
    issue = next(issue for issue in item.issues if issue.code == "FINANCIAL_OWNER_MISMATCH_owner-doc")
    assert issue.status == IssueStatus.OPEN and issue.severity == "BLOCKER"
    assert issue.related_document_ids == [doc.id] and expected_field in issue.detail
    assert "fraud" not in issue.detail.casefold()
    assert before == (item.profile.model_dump(), [e.model_dump() for e in item.evidence],
                      [d.model_dump() for d in item.documents])


@pytest.mark.parametrize(("doc_kind", "obs_kind", "subject", "full_name", "sponsor"), [
    ("bank_statement", "closing_balance", "ＳＡＭＰＬＥ   APPLICANT", "sample applicant", None),
    ("employment_letter", "salary", " sample applicant ", "Sample Applicant", None),
    ("sponsor_funds", "closing_balance", "SAMPLE SPONSOR", None, "Sample   Sponsor"),
])
def test_exact_nfkc_casefold_whitespace_owner_match_is_not_a_mismatch(
    doc_kind, obs_kind, subject, full_name, sponsor,
):
    doc = document("matching-doc", doc_kind)
    item = case(doc, full_name=full_name, sponsor=sponsor, evidence=[observation(
        "matching-observation", doc.id, kind=obs_kind, subject=subject,
        period="annual" if obs_kind == "salary" else "closing",
        account=None if obs_kind == "salary" else "ACCOUNT-01")])
    apply_financial_consistency_checks(item)
    assert not open_codes(item)


@pytest.mark.parametrize(("doc_kind", "obs_kind", "full_name", "sponsor"), [
    ("bank_statement", "closing_balance", None, "Sample Sponsor"),
    ("employment_letter", "salary", None, "Sample Sponsor"),
    ("sponsor_funds", "closing_balance", "Sample Applicant", None),
])
def test_unknown_expected_owner_remains_unknown_without_an_issue(doc_kind, obs_kind, full_name, sponsor):
    doc = document("unknown-owner", doc_kind)
    item = case(doc, full_name=full_name, sponsor=sponsor, evidence=[observation(
        "unknown-owner-observation", doc.id, kind=obs_kind, subject="Someone",
        period="annual" if obs_kind == "salary" else "closing",
        account=None if obs_kind == "salary" else "ACCOUNT-01")])
    apply_financial_consistency_checks(item)
    assert not item.issues


def test_matching_replacement_resolves_prior_owner_issue():
    doc = document("owner-resolution", "bank_statement")
    evidence = observation("owner-resolution-observation", doc.id, subject="Someone Else")
    item = case(doc, evidence=[evidence])
    apply_financial_consistency_checks(item)
    assert open_codes(item) == ["FINANCIAL_OWNER_MISMATCH_owner-resolution"]
    evidence.value["subject_name"] = "Sample Applicant"
    apply_financial_consistency_checks(item)
    assert item.issues[0].status == IssueStatus.RESOLVED


@pytest.mark.parametrize("change", ["document", "stale", "superseded", "malformed"])
def test_owner_issue_resolves_when_its_current_accepted_observation_disappears(change):
    doc = document("owner-lifecycle", "bank_statement")
    evidence = observation("owner-lifecycle-observation", doc.id, subject="Someone Else")
    item = case(doc, evidence=[evidence])
    apply_financial_consistency_checks(item)
    issue = next(issue for issue in item.issues if issue.code == "FINANCIAL_OWNER_MISMATCH_owner-lifecycle")
    if change == "document":
        doc.status = DocumentStatus.SUPERSEDED
    elif change == "stale":
        evidence.provenance_state = ProvenanceState.STALE
    elif change == "superseded":
        evidence.superseded = True
    else:
        evidence.value = {"malformed": True}
    apply_financial_consistency_checks(item)
    assert issue.status == IssueStatus.RESOLVED
    assert "no longer" in issue.resolution and "current" in issue.resolution


def test_repeated_check_does_not_duplicate_an_open_owner_issue():
    doc = document("owner-once", "bank_statement")
    item = case(doc, evidence=[observation("owner-once-observation", doc.id, subject="Someone Else")])
    apply_financial_consistency_checks(item)
    apply_financial_consistency_checks(item)
    assert [issue.code for issue in item.issues] == ["FINANCIAL_OWNER_MISMATCH_owner-once"]


def test_two_comparable_accepted_documents_with_different_amounts_block():
    one, two = document("statement-b", "bank_statement"), document("statement-a", "bank_statement")
    item = case(one, two, evidence=[
        observation("balance-one", one.id, amount="1200.00"),
        observation("balance-two", two.id, amount="1300.00"),
    ])
    apply_financial_consistency_checks(item)
    issues = [issue for issue in item.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_")]
    assert len(issues) == 1 and issues[0].status == IssueStatus.OPEN
    assert len(issues[0].code.removeprefix("FINANCIAL_OBSERVATION_CONFLICT_")) == 12
    assert issues[0].related_document_ids == ["statement-a", "statement-b"]
    assert all(part in issues[0].detail for part in
               ("closing_balance", "GBP", "closing", "unspecified", "2026-08-31", "same recorded account reference"))
    assert "ACCOUNT-01" not in issues[0].detail
    assert "fraud" not in issues[0].detail.casefold()


@pytest.mark.parametrize(("field", "change"), [
    ("kind", "salary"), ("subject_name", "Other Holder"), ("currency", "HKD"),
    ("period", "monthly"), ("basis", "net"), ("as_of", "2026-09-01"),
    ("account_reference", "ACCOUNT-02"), ("account_reference", None),
])
def test_noncomparable_dimensions_never_create_an_amount_conflict(field, change):
    one, two = document("scope-one", "bank_statement"), document("scope-two", "bank_statement")
    first = observation("scope-first", one.id)
    second = observation("scope-second", two.id, amount="9999.00")
    second.value[field] = change
    # Keep a cross-kind mutation otherwise well formed; document-kind validation
    # makes it unusable rather than forcing a comparison.
    if field == "kind":
        second.value.update(period="annual", account_reference="ACCOUNT-01")
    item = case(one, two, full_name=None, evidence=[first, second])
    apply_financial_consistency_checks(item)
    assert not any(code.startswith("FINANCIAL_OBSERVATION_CONFLICT_") for code in open_codes(item))


def test_no_currency_conversion_sum_or_profile_amount_comparison():
    one, two = document("gbp", "bank_statement"), document("hkd", "bank_statement")
    item = case(one, two, evidence=[observation("gbp-balance", one.id, amount="1000", currency="GBP"),
        observation("hkd-balance", two.id, amount="10000", currency="HKD")])
    item.profile.annual_income_gbp = 1000
    item.profile.estimated_trip_cost_gbp = 5000
    apply_financial_consistency_checks(item)
    assert not open_codes(item)
    assert item.profile.annual_income_gbp == 1000 and item.profile.estimated_trip_cost_gbp == 5000


@pytest.mark.parametrize("condition", ["one_document", "same_amount", "equivalent_decimal"])
def test_amount_difference_requires_two_documents_and_distinct_numeric_amounts(condition):
    one, two = document("amount-one", "bank_statement"), document("amount-two", "bank_statement")
    evidence = [observation("amount-first", one.id, amount="1200")]
    if condition == "one_document":
        evidence.append(observation("amount-second", one.id, amount="1300"))
    elif condition == "same_amount":
        evidence.append(observation("amount-second", two.id, amount="1200"))
    else:
        evidence.append(observation("amount-second", two.id, amount="1200.00"))
    item = case(one, two, evidence=evidence)
    apply_financial_consistency_checks(item)
    assert not any(code.startswith("FINANCIAL_OBSERVATION_CONFLICT_") for code in open_codes(item))


@pytest.mark.parametrize(("evidence_change", "doc_status"), [
    ({"superseded": True}, DocumentStatus.ACCEPTED_FOR_REVIEW),
    ({"state": ProvenanceState.STALE}, DocumentStatus.ACCEPTED_FOR_REVIEW),
    ({}, DocumentStatus.RECEIVED), ({}, DocumentStatus.PROCESSING),
    ({}, DocumentStatus.NEEDS_CLARIFICATION), ({}, DocumentStatus.SUPERSEDED),
])
def test_inactive_stale_or_nonaccepted_document_observations_are_ignored(evidence_change, doc_status):
    one = document("accepted", "bank_statement")
    two = document("ignored", "bank_statement", doc_status)
    ignored = observation("ignored-observation", two.id, amount="9999", **evidence_change)
    item = case(one, two, evidence=[observation("accepted-observation", one.id), ignored])
    apply_financial_consistency_checks(item)
    assert not open_codes(item)


@pytest.mark.parametrize("value", [
    None, [], "amount 1200", {},
    {"kind": "closing_balance", "subject_name": "Sample Applicant"},
    {"kind": "closing_balance", "subject_name": "", "amount": "1200", "currency": "GBP",
     "period": "closing", "basis": "unspecified", "as_of": "2026-08-31", "account_reference": "A1"},
    {"kind": "closing_balance", "subject_name": "Sample Applicant", "amount": "1,200", "currency": "GBP",
     "period": "closing", "basis": "unspecified", "as_of": "2026-08-31", "account_reference": "A1"},
    {"kind": "closing_balance", "subject_name": "Sample Applicant", "amount": "1200", "currency": "CAD",
     "period": "closing", "basis": "unspecified", "as_of": "2026-08-31", "account_reference": "A1"},
    {"kind": "closing_balance", "subject_name": "Sample Applicant", "amount": "1200", "currency": "GBP",
     "period": "closing", "basis": "unspecified", "as_of": None, "account_reference": "A1"},
    {"kind": "closing_balance", "subject_name": "Sample Applicant", "amount": "1200", "currency": "GBP",
     "period": "closing", "basis": "unspecified", "as_of": "31/08/2026", "account_reference": "A1"},
    {"kind": "closing_balance", "subject_name": "Sample Applicant", "amount": "1200", "currency": "GBP",
     "period": "closing", "basis": "unspecified", "as_of": "2026-08-31", "account_reference": "A1", "extra": True},
])
def test_malformed_observation_is_skipped_without_crashing_or_mutation(value):
    doc = document("malformed", "bank_statement")
    evidence = observation("malformed-observation", doc.id)
    evidence.value = value
    item = case(doc, evidence=[evidence])
    before = item.model_dump_json()
    apply_financial_consistency_checks(item)
    assert not item.issues and item.model_dump_json() == before


def test_same_group_becoming_consistent_resolves_the_exact_prior_issue():
    one, two = document("resolve-one", "bank_statement"), document("resolve-two", "bank_statement")
    first = observation("resolve-first", one.id, amount="1200")
    second = observation("resolve-second", two.id, amount="1400")
    item = case(one, two, evidence=[first, second])
    apply_financial_consistency_checks(item)
    conflict = next(issue for issue in item.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    code = conflict.code
    second.value["amount"] = "1200.00"
    apply_financial_consistency_checks(item)
    assert len(item.issues) == 1 and item.issues[0].code == code
    assert item.issues[0].status == IssueStatus.RESOLVED


def test_malformed_observation_resolves_prior_conflict_as_no_longer_comparable():
    one, two = document("malformed-one", "bank_statement"), document("malformed-two", "bank_statement")
    first = observation("malformed-first", one.id, amount="1200")
    second = observation("malformed-second", two.id, amount="1400")
    item = case(one, two, evidence=[first, second])
    apply_financial_consistency_checks(item)
    issue = next(issue for issue in item.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    second.value = {"malformed": True}
    apply_financial_consistency_checks(item)
    assert issue.status == IssueStatus.RESOLVED
    assert "no longer" in issue.resolution and "comparable" in issue.resolution


@pytest.mark.parametrize(("change", "value"), [
    ("document_status", DocumentStatus.SUPERSEDED),
    ("evidence_state", ProvenanceState.STALE),
    ("evidence_superseded", True),
    ("as_of", "2026-09-01"),
    ("account_reference", "ACCOUNT-02"),
])
def test_obsolete_or_rebased_group_resolves_prior_conflict_without_calling_it_consistent(change, value):
    one, two = document("lifecycle-one", "bank_statement"), document("lifecycle-two", "bank_statement")
    first = observation("lifecycle-first", one.id, amount="1200")
    second = observation("lifecycle-second", two.id, amount="1400")
    item = case(one, two, evidence=[first, second])
    apply_financial_consistency_checks(item)
    issue = next(issue for issue in item.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    if change == "document_status":
        two.status = value
    elif change == "evidence_state":
        second.provenance_state = value
    elif change == "evidence_superseded":
        second.superseded = value
    else:
        second.value[change] = value
    apply_financial_consistency_checks(item)
    assert issue.status == IssueStatus.RESOLVED
    assert "no longer" in issue.resolution and "comparable" in issue.resolution


@pytest.mark.parametrize(("field", "value"), [
    ("as_of", "2026-09-01"), ("period", "monthly"), ("basis", "net"),
])
def test_salary_observations_compare_without_accounts_but_keep_date_period_and_basis(field, value):
    one, two = document("salary-one", "employment_letter"), document("salary-two", "employment_letter")
    second = observation("salary-second", two.id, kind="salary", amount="48000", period="annual",
        basis="gross", account=None)
    second.value[field] = value
    item = case(one, two, evidence=[
        observation("salary-first", one.id, kind="salary", amount="46000", period="annual",
            basis="gross", account=None), second,
    ])
    apply_financial_consistency_checks(item)
    assert not any(code.startswith("FINANCIAL_OBSERVATION_CONFLICT_") for code in open_codes(item))


def test_two_salary_documents_same_basis_date_and_subject_can_conflict_without_account_reference():
    one, two = document("salary-a", "employment_letter"), document("salary-b", "employment_letter")
    item = case(one, two, evidence=[
        observation("salary-a-observation", one.id, kind="salary", amount="46000", period="annual",
            basis="gross", account=None),
        observation("salary-b-observation", two.id, kind="salary", amount="48000", period="annual",
            basis="gross", account=None),
    ])
    apply_financial_consistency_checks(item)
    issue = next(issue for issue in item.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    assert issue.status == IssueStatus.OPEN
    assert "same salary date and basis" in issue.detail
    assert "account reference" not in issue.detail


def test_closing_balance_with_salary_basis_is_malformed_not_comparable():
    one, two = document("balance-a", "bank_statement"), document("balance-b", "bank_statement")
    item = case(one, two, evidence=[observation("balance-a-observation", one.id),
        observation("balance-b-observation", two.id, amount="9999", basis="gross")])
    apply_financial_consistency_checks(item)
    assert not open_codes(item)


def test_group_hash_and_related_documents_are_stable_across_input_order_and_nfkc():
    one, two = document("stable-z", "bank_statement"), document("stable-a", "bank_statement")
    evidence = [observation("stable-one", one.id, subject="ＳＡＭＰＬＥ  Applicant", account="ＡＣＣ  01"),
        observation("stable-two", two.id, subject="sample applicant", account="acc 01", amount="1300")]
    left = case(one, two, evidence=evidence)
    right = case(two.model_copy(deep=True), one.model_copy(deep=True),
        evidence=[item.model_copy(deep=True) for item in reversed(evidence)])
    apply_financial_consistency_checks(left)
    apply_financial_consistency_checks(right)
    left_issue = next(issue for issue in left.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    right_issue = next(issue for issue in right.issues if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    assert left_issue.code == right_issue.code
    assert left_issue.related_document_ids == right_issue.related_document_ids == ["stable-a", "stable-z"]


def test_nonfinancial_evidence_is_unchanged_and_ignored():
    doc = document("ordinary-evidence", "bank_statement")
    ordinary = Evidence(id="ordinary", fact_key="full_name", value="Someone Else",
        source_event_id="ordinary-event", source_document_id=doc.id, source_excerpt="Someone Else",
        extraction_method="synthetic", model_version="synthetic", confidence=1)
    item = case(doc, evidence=[ordinary])
    before = item.model_dump_json()
    apply_financial_consistency_checks(item)
    assert not item.issues and item.model_dump_json() == before


@pytest.mark.parametrize("change", [
    "low_confidence", "missing_page", "amount_semantics", "subject_binding",
    "date_semantics", "account_binding", "inactive_state",
])
def test_persisted_observation_revalidates_provenance_and_excerpt_semantics(change):
    item = observation("persisted-check", "persisted-doc")
    if change == "low_confidence":
        item.confidence = 0.5
    elif change == "missing_page":
        item.page = None
    elif change == "amount_semantics":
        item.source_excerpt = "Transaction GBP 1200.00"
    elif change == "subject_binding":
        item.value["subject_excerpt"] = "Account holder: Alice. Adviser: Sample Applicant"
    elif change == "date_semantics":
        item.value["date_excerpt"] = "Document generated 2026-08-31"
    elif change == "account_binding":
        item.value["account_reference"] = "2026"
        item.value["account_excerpt"] = "Statement date 2026-08-31"
    else:
        item.provenance_state = ProvenanceState.STALE
    assert not financial_observation_is_valid(item, "bank_statement")
