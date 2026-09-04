from pathlib import Path

import pytest
from reportlab.pdfgen import canvas

from visa_agent.documents.natural import (
    DocumentFact,
    DocumentProposal,
    FinancialObservation,
    NaturalPDFReader,
    validate_document,
)

TEXT = "Conference invitation for Lin Chen. The event begins on 2026-11-10 and ends on 2026-11-12."


@pytest.mark.parametrize("warning", [
    "SAMPLE PASSPORT — NOT VALID FOR TRAVEL", "SPECIMEN", "For demonstration purposes only",
    "ＤＵＭＭＹ ＰＡＳＳＰＯＲＴ", "护照样张，仅供演示", "仅供测试，不可用于旅行",
])
def test_explicit_identity_specimens_cannot_satisfy_document_requirements(warning):
    text = ("Passport. Name: Lin Chen. Born: 1995-02-03. Expiry: 2030-12-31.\n" + warning)
    candidate = DocumentProposal(kind="passport", language="en", classification_page=1,
        classification_excerpt="Passport.", confidence=0.99, facts=[
            DocumentFact(field="full_name", value="Lin Chen", page=1, excerpt="Name: Lin Chen", confidence=0.99),
            DocumentFact(field="date_of_birth", value="1995-02-03", page=1, excerpt="Born: 1995-02-03", confidence=0.99),
            DocumentFact(field="passport_expiry_date", value="2030-12-31", page=1, excerpt="Expiry: 2030-12-31", confidence=0.99),
        ])
    result = validate_document(candidate, [text], method="text", version="fake-high-confidence")
    assert result.requires_review
    assert result.review_reason == "Specimen is not an identity document"


def proposal() -> DocumentProposal:
    return DocumentProposal(
        kind="conference_invitation",
        language="en",
        classification_page=1,
        classification_excerpt="Conference invitation for Lin Chen.",
        confidence=0.99,
        facts=[
            DocumentFact(
                field="full_name", value="Lin Chen", page=1, excerpt="for Lin Chen", confidence=0.99
            ),
            DocumentFact(
                field="invitation_event_end_date",
                value="2026-11-12",
                page=1,
                excerpt="ends on 2026-11-12",
                confidence=0.99,
            ),
            DocumentFact(
                field="invitation_event_start_date",
                value="2026-11-10",
                page=1,
                excerpt="begins on 2026-11-10",
                confidence=0.99,
            ),
        ],
    )


def test_natural_evidence_retains_page_excerpt_and_model_provenance() -> None:
    result = validate_document(
        proposal(), [TEXT], method="bounded_pdf_text_extraction", version="fake"
    )
    assert result.facts["full_name"] == ("Lin Chen", 1, "for Lin Chen")
    assert result.model_version == "fake"
    assert result.method != "deterministic_pdf_fixture_extractor"
    assert not result.requires_review


@pytest.mark.parametrize(
    "change", ["invented_excerpt", "wrong_page", "invented_date", "low_confidence", "invented_name"]
)
def test_document_proposals_cannot_invent_page_evidence(change: str) -> None:
    candidate = proposal()
    if change == "invented_excerpt":
        candidate.classification_excerpt = "Approved by the UK Government"
    elif change == "wrong_page":
        candidate.facts[0].page = 2
    elif change == "invented_date":
        candidate.facts[1].value = "2027-11-12"
    elif change == "low_confidence":
        candidate.facts[0].confidence = 0.5
    else:
        candidate.facts[0].value = "Someone Else"
    with pytest.raises(ValueError):
        validate_document(candidate, [TEXT], method="text", version="fake")


def test_ordinary_pdf_needs_no_machine_markers(tmp_path: Path) -> None:
    path = tmp_path / "invitation.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 750, TEXT)
    pdf.save()

    class Model:
        version = "fake"

        def extract_document(self, pages: list[str]) -> DocumentProposal:
            assert "DOCUMENT_KIND=" not in pages[0]
            return proposal()

    result = NaturalPDFReader(Model())(path)
    assert result.kind == "conference_invitation"
    assert not result.requires_review


def test_provider_failure_retains_document_for_review(tmp_path: Path) -> None:
    path = tmp_path / "letter.pdf"
    pdf = canvas.Canvas(str(path))
    pdf.drawString(40, 750, TEXT)
    pdf.save()

    class Model:
        version = "unavailable"

        def extract_document(self, pages: list[str]) -> DocumentProposal:
            raise TimeoutError("provider unavailable")

    result = NaturalPDFReader(Model())(path)
    assert result.requires_review and result.facts == {}


def test_missing_invitee_is_held_even_when_model_claims_confidence() -> None:
    candidate = proposal()
    candidate.facts = candidate.facts[1:]
    result = validate_document(candidate, [TEXT], method="text", version="fake")
    assert result.requires_review
    assert "full_name" in (result.review_reason or "")


def financial(kind="closing_balance", **changes) -> FinancialObservation:
    values = dict(kind=kind, subject_name="Lin Chen", amount="12500.50", currency="GBP",
        period="closing" if kind == "closing_balance" else "annual",
        basis="unspecified" if kind == "closing_balance" else "gross",
        as_of="2026-08-31", account_reference="7788", subject_page=1,
        subject_excerpt="Account holder Lin Chen, account ending 7788",
        amount_page=1,
        amount_excerpt="closing balance GBP 12,500.50.",
        date_page=1, date_excerpt="Statement date 2026-08-31.",
        account_page=1, account_excerpt="account ending 7788",
        confidence=0.99)
    values.update(changes)
    return FinancialObservation(**values)


def financial_proposal(kind="bank_statement", observation=None) -> DocumentProposal:
    item = observation or financial()
    return DocumentProposal(kind=kind, language="en", classification_page=1,
        classification_excerpt="Bank statement", confidence=0.99,
        facts=[DocumentFact(field="full_name", value="Lin Chen", page=1,
            excerpt="Account holder Lin Chen", confidence=0.99)],
        financial_observations=[item])


FINANCIAL_TEXT = (
    "Bank statement. Account holder Lin Chen, account ending 7788, "
    "closing balance GBP 12,500.50. Statement date 2026-08-31."
)


def test_grounded_financial_observation_preserves_currency_date_and_account() -> None:
    result = validate_document(financial_proposal(), [FINANCIAL_TEXT], method="text", version="fake")
    assert result.financial_observations == (financial(),)
    assert result.facts["full_name"][0] == "Lin Chen"
    assert not result.requires_review


@pytest.mark.parametrize("change", [
    {"subject_name": "Someone Else"}, {"amount": "9999"}, {"currency": "USD"},
    {"as_of": "2026-09-01"}, {"account_reference": "9999"}, {"confidence": 0.5},
    {"period": "annual"},
])
def test_financial_observation_must_be_fully_grounded_and_well_typed(change) -> None:
    with pytest.raises(ValueError):
        validate_document(financial_proposal(observation=financial(**change)), [FINANCIAL_TEXT],
                          method="text", version="fake")


@pytest.mark.parametrize("kind,expected", [
    ("bank_statement", "closing_balance"), ("employment_letter", "salary"),
    ("sponsor_funds", "closing_balance"),
])
def test_financial_document_without_expected_amount_is_held(kind, expected) -> None:
    facts = [] if kind == "sponsor_funds" else [
        DocumentFact(field="full_name", value="Lin Chen", page=1,
                     excerpt="Lin Chen", confidence=0.99)
    ]
    result = validate_document(DocumentProposal(kind=kind, language="en", classification_page=1,
        classification_excerpt="Lin Chen", confidence=0.99, facts=facts),
        ["Lin Chen employment and financial document"], method="text", version="fake")
    assert result.requires_review
    assert result.review_reason == f"Missing grounded financial observation: {expected}"


def test_sponsor_holder_is_never_stored_as_applicant_name() -> None:
    candidate = financial_proposal(kind="sponsor_funds")
    with pytest.raises(ValueError, match="sponsor account holder"):
        validate_document(candidate, [FINANCIAL_TEXT], method="text", version="fake")


def test_salary_cannot_be_relabelled_as_a_bank_balance() -> None:
    salary = financial("salary", period="annual", basis="gross", account_reference=None,
        subject_excerpt="Employee Lin Chen",
        amount_excerpt="gross annual salary GBP 12,500.50.", account_page=None,
        account_excerpt=None, date_excerpt="letter dated 2026-08-31.")
    candidate = financial_proposal(observation=salary)
    candidate.facts[0].excerpt = "Lin Chen"
    with pytest.raises(ValueError, match="document kind"):
        validate_document(candidate, ["Bank statement. " + salary.subject_excerpt + " "
                          + salary.amount_excerpt + " " + salary.date_excerpt],
                          method="text", version="fake")


@pytest.mark.parametrize(("amount", "currency", "amount_excerpt"), [
    ("100", "GBP", "closing balance GBP 5,100 as of 2026-08-31."),
    ("12500.50", "GBP", "closing balance GBP -12,500.50 as of 2026-08-31."),
    ("-12500.50", "GBP", "closing balance GBP 12,500.50 as of 2026-08-31."),
    ("1000", "USD", "closing balance HKD 1,000; USD 200 as of 2026-08-31."),
    ("12500.50", "CNY", "closing balance ¥ 12,500.50 as of 2026-08-31."),
])
def test_amount_currency_and_sign_cannot_be_cross_bound_or_partially_matched(
    amount, currency, amount_excerpt,
) -> None:
    observation = financial(amount=amount, currency=currency, amount_excerpt=amount_excerpt)
    candidate = financial_proposal(observation=observation)
    text = "Bank statement. " + " ".join((observation.subject_excerpt,
        amount_excerpt, observation.date_excerpt, observation.account_excerpt or ""))
    with pytest.raises(ValueError, match="financial observation"):
        validate_document(candidate, [text], method="text", version="fake")


def test_subject_amount_date_and_account_may_have_separate_grounded_pages() -> None:
    observation = financial(amount_page=2, date_page=2)
    pages = ["Bank statement. " + observation.subject_excerpt,
             observation.amount_excerpt + " " + observation.date_excerpt + " End of statement."]
    result = validate_document(financial_proposal(observation=observation), pages,
                               method="text", version="fake")
    assert result.financial_observations[0].subject_page == 1
    assert result.financial_observations[0].amount_page == 2


@pytest.mark.parametrize(("changes", "text"), [
    (
        {"subject_name": "Kai"},
        "Bank statement. Account holder: Alice. Adviser: Kai. Account ending 7788. "
        "Closing balance GBP 12,500.50. Statement date 2026-08-31.",
    ),
    (
        {"subject_name": "Ann", "subject_excerpt": "Account holder: Joanne"},
        "Bank statement. Account holder: Joanne. Account ending 7788. "
        "Closing balance GBP 12,500.50. Statement date 2026-08-31.",
    ),
    (
        {"account_reference": "2026", "account_excerpt": "Statement date 2026-08-31"},
        "Bank statement. Account holder Lin Chen. Statement date 2026-08-31. "
        "Closing balance GBP 12,500.50.",
    ),
    (
        {"date_excerpt": "Document generated 2026-08-31"},
        "Bank statement. Account holder Lin Chen. Account ending 7788. "
        "Closing balance GBP 12,500.50. Document generated 2026-08-31.",
    ),
])
def test_financial_roles_must_bind_to_their_exact_values(changes, text) -> None:
    item = financial(**changes)
    candidate = financial_proposal(observation=item)
    candidate.facts[0].excerpt = "Lin Chen"
    if "Lin Chen" not in text:
        text += " Applicant record: Lin Chen."
    with pytest.raises(ValueError, match="financial observation"):
        validate_document(candidate, [text], method="text", version="fake")
