"""Offline financial-document journeys through PDF reading, workflow and Gmail SENT.

Every name and document in this module is synthetic. These tests check bounded
extraction and consistency review; they do not authenticate a document, decide
funding sufficiency, or establish an applicant's real-world identity.
"""

from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from reportlab.pdfgen import canvas

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.documents.natural import (
    DocumentFact,
    DocumentKind,
    DocumentProposal,
    FinancialBasis,
    FinancialObservation,
    FinancialObservationKind,
    FinancialPeriod,
    NaturalPDFReader,
)
from visa_agent.domain.models import (
    Case,
    DocumentStatus,
    InboundEvent,
    IssueStatus,
    ProvenanceState,
)
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore

TODAY = date(2026, 9, 5)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
SENDER = "financial-journey@example.test"
APPLICANT = "Example Applicant"
SPONSOR = "Example Sponsor"
MODEL_VERSION = "offline-financial-document-v1"


class CaseModel:
    def __init__(self, proposal: CasePatch) -> None:
        self.proposal = proposal
        self.events: list[InboundEvent] = []

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.events.append(event.model_copy(deep=True))
        return self.proposal.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


class DocumentModel:
    version = MODEL_VERSION

    def __init__(self, proposals: list[DocumentProposal]) -> None:
        self.proposals = [item.model_copy(deep=True) for item in proposals]
        self.pages_seen: list[list[str]] = []

    def extract_document(self, pages: list[str]) -> DocumentProposal:
        self.pages_seen.append(list(pages))
        text = "\n".join(pages).casefold()
        assert "document_kind=" not in text and "fact:" not in text
        matches = [item for item in self.proposals if item.classification_excerpt.casefold() in text]
        assert len(matches) == 1
        return matches[0].model_copy(deep=True)


class CapturedGmail(GmailAdapter):
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def send_reply(
        self,
        recipient: str,
        subject: str,
        body: str,
        thread_id: str,
        in_reply_to: str,
        references: str,
        message_id: str,
        attachment: tuple[str, bytes] | None = None,
    ) -> dict[str, Any]:
        assert recipient == SENDER and attachment is None
        self.requests.append({
            "recipient": recipient,
            "subject": subject,
            "body": body,
            "thread_id": thread_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "message_id": message_id,
            "attachment": attachment,
        })
        return {"id": f"captured-financial-{len(self.requests)}"}


def empty_patch() -> CasePatch:
    return CasePatch(updates=[], ambiguities=[], customer_questions=[])


def name_patch(name: str = APPLICANT) -> CasePatch:
    excerpt = f"My name is {name}."
    return CasePatch(
        updates=[FactUpdate(field="full_name", value=name, source_excerpt=excerpt, confidence=1)],
        ambiguities=[],
        customer_questions=[],
    )


def plain_pdf(path: Path, *lines: str) -> Path:
    """Write ordinary visible text, with no fixture protocol or extraction markers."""
    pdf = canvas.Canvas(str(path))
    for index, line in enumerate(lines):
        pdf.drawString(40, 760 - index * 24, line)
    pdf.save()
    return path


def observation(
    *,
    kind: FinancialObservationKind = "closing_balance",
    subject: str = APPLICANT,
    amount: str = "1250.50",
    currency: str = "GBP",
    period: FinancialPeriod = "closing",
    basis: FinancialBasis = "unspecified",
    as_of: date = date(2026, 8, 31),
    account: str | None = "account ending 1234",
    subject_excerpt: str | None = None,
    amount_excerpt: str | None = None,
    date_excerpt: str | None = None,
    account_excerpt: str | None = None,
) -> FinancialObservation:
    subject_role = "Account holder" if kind == "closing_balance" else "Employee"
    subject_excerpt = subject_excerpt or f"{subject_role} {subject}; {account or 'payroll record'}."
    if amount_excerpt is None:
        formatted_amount = f"{Decimal(amount):,.2f}"
        if kind == "closing_balance":
            amount_excerpt = (
                f"Closing balance {currency} {formatted_amount} as of {as_of.isoformat()}; {account}."
            )
        else:
            basis_text = "" if basis == "unspecified" else f"{basis.title()} "
            period_text = "annual" if period == "annual" else "monthly"
            amount_excerpt = (
                f"{basis_text}{period_text} salary {currency} {formatted_amount} "
                f"as recorded by the employer."
            )
    date_label = "Statement date" if kind == "closing_balance" else "Letter date"
    date_excerpt = date_excerpt or f"{date_label}: {as_of.isoformat()}."
    account_excerpt = account_excerpt or (f"Recorded account reference: {account}." if account else None)
    return FinancialObservation(
        kind=kind,
        subject_name=subject,
        amount=amount,
        currency=cast(Any, currency),
        period=period,
        basis=basis,
        as_of=as_of,
        account_reference=account,
        subject_page=1,
        subject_excerpt=subject_excerpt,
        amount_page=1,
        amount_excerpt=amount_excerpt,
        date_page=1,
        date_excerpt=date_excerpt,
        account_page=1 if account else None,
        account_excerpt=account_excerpt,
        confidence=0.99,
    )


def financial_proposal(
    identifier: str,
    *,
    document_kind: DocumentKind = "bank_statement",
    item: FinancialObservation | None = None,
    holder: str = APPLICANT,
    include_name_fact: bool | None = None,
) -> DocumentProposal:
    item = item or observation(subject=holder)
    if include_name_fact is None:
        include_name_fact = document_kind != "sponsor_funds"
    facts = (
        [DocumentFact(field="full_name", value=holder, page=1,
                      excerpt=f"Account holder {holder}" if document_kind != "employment_letter"
                      else f"Employee {holder}", confidence=0.99)]
        if include_name_fact else []
    )
    return DocumentProposal(
        kind=document_kind,
        language="en",
        classification_page=1,
        classification_excerpt=identifier,
        confidence=0.99,
        facts=facts,
        financial_observations=[item],
    )


def document_lines(proposal: DocumentProposal) -> tuple[str, ...]:
    lines = [proposal.classification_excerpt]
    lines.extend(item.excerpt for item in proposal.facts)
    for item in proposal.financial_observations:
        lines.extend((item.subject_excerpt, item.amount_excerpt, item.date_excerpt))
        if item.account_excerpt:
            lines.append(item.account_excerpt)
    # Preserve order while removing exact duplicates such as a name fact that is
    # already a substring of the subject locator.
    return tuple(dict.fromkeys(lines))


class Journey:
    def __init__(self, tmp_path: Path) -> None:
        self.db_path = tmp_path / "financial-journey.db"
        self.gmail = CapturedGmail()
        self.turn_number = 0

    def turn(
        self,
        body: str,
        patch: CasePatch | None = None,
        *,
        documents: list[tuple[Path, DocumentProposal]] | None = None,
    ) -> SimpleNamespace:
        from visa_agent.workflow.service import WorkflowService

        self.turn_number += 1
        documents = documents or []
        event = InboundEvent(
            id=f"financial-event-{self.turn_number}",
            channel="gmail",
            external_thread_id="financial-journey-thread",
            sender=SENDER,
            subject="Synthetic financial document journey",
            body=body,
            attachment_paths=[str(path) for path, _ in documents],
            rfc_message_id=f"<financial-event-{self.turn_number}@example.test>",
            received_at=datetime(2026, 9, 5, 10, tzinfo=UTC)
            + timedelta(minutes=self.turn_number),
        )
        case_model = CaseModel(patch or empty_patch())
        document_model = DocumentModel([proposal for _, proposal in documents])
        with closing(SQLiteStore(self.db_path)) as store:
            guard = GuardedLLM(case_model)
            case, duplicate, plan = WorkflowService(
                store,
                POLICY,
                guard,
                today_provider=lambda: TODAY,
                document_reader=NaturalPDFReader(document_model),
            ).process(event)
            assert not duplicate and plan == "blocked" and not guard.last_extraction_fallback
            sender = AutomaticGmailReplySender(self.gmail, store, SENDER)
            sender.withhold_obsolete_unsent()
            outcomes = OutboxDispatcher(store, sender, channel="gmail").dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["reply_render_mode"] == "reviewed"
            assert row["provider_message_id"]
            assert row["payload"] == self.gmail.requests[-1]["body"]
            persisted = store.get_case(case.id)
            assert persisted is not None and persisted.model_dump() == case.model_dump()
            return SimpleNamespace(
                case=case.model_copy(deep=True),
                event=event,
                body=row["payload"],
                document_model=document_model,
            )

    def known_applicant(self) -> SimpleNamespace:
        return self.turn(f"My name is {APPLICANT}.", name_patch())

    def reopen(self, case_id: str) -> Case:
        with closing(SQLiteStore(self.db_path)) as store:
            case = store.get_case(case_id)
            assert case is not None
            return case


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Financial document tests cannot use network I/O")

    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def attach(tmp_path: Path, name: str, proposal: DocumentProposal) -> tuple[Path, DocumentProposal]:
    path = plain_pdf(tmp_path / f"{name}.pdf", *document_lines(proposal))
    return path, proposal


def assert_financial_profile_unchanged(case: Case) -> None:
    assert case.profile.full_name == APPLICANT
    assert case.profile.annual_income_gbp is None
    assert case.profile.estimated_trip_cost_gbp is None
    assert case.profile.funding_source is None
    assert case.profile.sponsor_name is None
    assert case.profile.sponsor_relationship is None
    assert case.profile.sponsor_is_in_uk is None


@pytest.mark.parametrize("document_kind", ["bank_statement", "employment_letter"])
def test_plain_pdf_financial_observation_persists_structured_provenance_after_reopen(
    tmp_path: Path, document_kind: str,
) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    if document_kind == "bank_statement":
        item = observation()
        identifier = "North Harbour Bank current account statement"
    else:
        item = observation(
            kind="salary",
            amount="42000.00",
            period="annual",
            basis="gross",
            account="payroll reference NH-77",
        )
        identifier = "North Harbour Trading employment and salary letter"
    proposal = financial_proposal(
        identifier, document_kind=cast(DocumentKind, document_kind), item=item
    )

    result = journey.turn(
        "I am attaching this financial document for review.",
        documents=[attach(tmp_path, document_kind, proposal)],
    )

    assert len(result.document_model.pages_seen) == 1
    assert all("fact:" not in page.casefold() and "document_kind=" not in page.casefold()
               for page in result.document_model.pages_seen[0])
    document = result.case.documents[-1]
    assert document.kind == document_kind
    assert document.status == DocumentStatus.ACCEPTED_FOR_REVIEW
    evidence = next(item for item in result.case.evidence
                    if item.fact_key == "financial_observation")
    assert evidence.value == item.model_dump(
        mode="json", exclude={"amount_page", "amount_excerpt", "confidence"}
    )
    assert evidence.source_event_id == result.event.id
    assert evidence.source_document_id == document.id
    assert evidence.source_excerpt == item.amount_excerpt and evidence.page == item.amount_page == 1
    assert evidence.extraction_method == "bounded_pdf_text_extraction"
    assert evidence.model_version == MODEL_VERSION and evidence.confidence == item.confidence
    assert evidence.provenance_state == ProvenanceState.EXTRACTED_UNVERIFIED
    assert evidence.value["subject_excerpt"] == item.subject_excerpt
    assert evidence.value["subject_page"] == item.subject_page == 1
    assert "amount_excerpt" not in evidence.value and "amount_page" not in evidence.value
    assert_financial_profile_unchanged(result.case)

    reopened = journey.reopen(result.case.id)
    reopened_evidence = next(item for item in reopened.evidence if item.id == evidence.id)
    assert reopened_evidence.model_dump() == evidence.model_dump()
    assert reopened.documents[-1].model_dump() == document.model_dump()
    assert_financial_profile_unchanged(reopened)


def test_sponsor_funds_holder_is_evidence_subject_not_applicant_name(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    item = observation(subject=SPONSOR, amount="9000.00", account="account ending 9876")
    proposal = financial_proposal(
        "North Harbour Bank sponsor funds statement",
        document_kind="sponsor_funds",
        item=item,
        holder=SPONSOR,
    )
    result = journey.turn(
        "This is the account statement supplied by my sponsor.",
        documents=[attach(tmp_path, "sponsor-funds", proposal)],
    )

    evidence = next(item for item in result.case.evidence
                    if item.fact_key == "financial_observation")
    assert evidence.value["subject_name"] == SPONSOR
    assert all(item.value != SPONSOR for item in result.case.active_evidence("full_name"))
    assert_financial_profile_unchanged(result.case)


def test_bank_holder_mismatch_blocks_gate_without_overwriting_applicant(tmp_path: Path) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    other = "Different Example Holder"
    item = observation(subject=other)
    proposal = financial_proposal(
        "North Harbour Bank differently held account",
        item=item,
        holder=other,
    )
    result = journey.turn(
        "Here is a bank statement for review.",
        documents=[attach(tmp_path, "different-holder", proposal)],
    )

    document = result.case.documents[-1]
    owner_issue = next(issue for issue in result.case.issues
                       if issue.code == f"FINANCIAL_OWNER_MISMATCH_{document.id}")
    assert owner_issue.status == IssueStatus.OPEN and owner_issue.severity == "BLOCKER"
    assert owner_issue.related_document_ids == [document.id]
    gate = evaluate_gate(result.case.model_copy(deep=True), POLICY, TODAY)
    assert gate.checks["no_unresolved_blocker_issue"] is False and not gate.allowed
    assert result.case.delivery_path is None
    assert_financial_profile_unchanged(result.case)


def test_comparable_different_balances_create_redacted_blocker_and_prevent_delivery(
    tmp_path: Path,
) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    secret_reference = "confidential-account-ending-1234"
    first = observation(amount="1250.50", account=secret_reference)
    second = observation(amount="1300.00", account=secret_reference)
    proposals = [
        financial_proposal("North Harbour Bank statement one", item=first),
        financial_proposal("North Harbour Bank statement two", item=second),
    ]
    result = journey.turn(
        "Here are two bank statements for review.",
        documents=[attach(tmp_path, f"comparable-{index}", proposal)
                   for index, proposal in enumerate(proposals, 1)],
    )

    issue = next(issue for issue in result.case.issues
                 if issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_"))
    assert issue.status == IssueStatus.OPEN and issue.severity == "BLOCKER"
    assert set(issue.related_document_ids) == {document.id for document in result.case.documents[-2:]}
    assert secret_reference.casefold() not in issue.detail.casefold()
    assert "same recorded account reference" in issue.detail
    assert "funding-sufficiency decision" in issue.detail
    gate = evaluate_gate(result.case.model_copy(deep=True), POLICY, TODAY)
    assert gate.checks["no_unresolved_blocker_issue"] is False and not gate.allowed
    assert result.case.delivery_path is None
    assert_financial_profile_unchanged(result.case)


@pytest.mark.parametrize("different_dimension", [
    "date", "currency", "account", "period", "basis", "subject",
])
def test_noncomparable_amounts_do_not_create_false_financial_conflict(
    tmp_path: Path, different_dimension: str,
) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    common: dict[str, Any] = dict(amount="1250.50", account="shared reference 1234")
    changed: dict[str, Any] = dict(amount="9999.00", account="shared reference 1234")
    document_kind: DocumentKind = "bank_statement"
    holders = [APPLICANT, APPLICANT]
    if different_dimension == "date":
        changed["as_of"] = date(2026, 8, 30)
    elif different_dimension == "currency":
        changed["currency"] = "USD"
    elif different_dimension == "account":
        changed["account"] = "different reference 5678"
    elif different_dimension in {"period", "basis"}:
        document_kind = "employment_letter"
        common.update(kind="salary", period="annual", basis="gross")
        changed.update(kind="salary", period="monthly" if different_dimension == "period" else "annual",
                       basis="gross" if different_dimension == "period" else "net")
    else:
        document_kind = "sponsor_funds"
        holders[1] = "Other Example Sponsor"
        common["subject"] = holders[0]
        changed["subject"] = holders[1]
    items = [observation(**common), observation(**changed)]
    proposals = [
        financial_proposal(
            f"North Harbour {different_dimension} comparison {index}",
            document_kind=document_kind,
            item=item,
            holder=holders[index - 1],
        )
        for index, item in enumerate(items, 1)
    ]

    result = journey.turn(
        "Please review these two financial documents.",
        documents=[attach(tmp_path, f"{different_dimension}-{index}", proposal)
                   for index, proposal in enumerate(proposals, 1)],
    )

    assert not any(issue.status == IssueStatus.OPEN
                   and issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_")
                   for issue in result.case.issues)
    assert_financial_profile_unchanged(result.case)


@pytest.mark.parametrize(("document_kind", "expected_kind"), [
    ("bank_statement", "closing_balance"),
    ("employment_letter", "salary"),
])
def test_financial_document_missing_required_observation_is_held_for_human_review(
    tmp_path: Path, document_kind: str, expected_kind: str,
) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    holder_role = "Employee" if document_kind == "employment_letter" else "Account holder"
    identifier = f"North Harbour {document_kind} without a grounded amount"
    proposal = DocumentProposal(
        kind=cast(DocumentKind, document_kind),
        language="en",
        classification_page=1,
        classification_excerpt=identifier,
        confidence=0.99,
        facts=[DocumentFact(field="full_name", value=APPLICANT, page=1,
                            excerpt=f"{holder_role} {APPLICANT}", confidence=0.99)],
        financial_observations=[],
    )
    result = journey.turn(
        "Please review this document.",
        documents=[attach(tmp_path, f"missing-{document_kind}", proposal)],
    )

    document = result.case.documents[-1]
    assert document.status == DocumentStatus.HUMAN_REVIEW_REQUIRED
    issue = next(issue for issue in result.case.open_blockers()
                 if document.id in issue.related_document_ids)
    assert expected_kind in issue.detail
    assert not any(item.fact_key == "financial_observation"
                   and item.source_document_id == document.id for item in result.case.evidence)
    assert_financial_profile_unchanged(result.case)


@pytest.mark.parametrize("forgery", [
    "subject_excerpt", "amount_excerpt", "amount_boundary", "negative_sign",
    "currency_binding", "subject", "date", "sponsor_full_name",
])
def test_ungrounded_or_misclassified_financial_proposal_is_held_without_evidence(
    tmp_path: Path, forgery: str,
) -> None:
    journey = Journey(tmp_path)
    journey.known_applicant()
    document_kind = "bank_statement"
    holder = APPLICANT
    item = observation()
    include_name_fact: bool | None = None
    if forgery == "subject_excerpt":
        item = item.model_copy(update={"subject_excerpt": "Account holder Invented Name."})
    elif forgery == "amount_excerpt":
        item = item.model_copy(update={"amount_excerpt": "Closing balance GBP 9,999.00."})
    elif forgery == "amount_boundary":
        item = item.model_copy(update={
            "amount": "100",
            "amount_excerpt": "Closing balance GBP 5,100 as of 2026-08-31; account ending 1234.",
        })
    elif forgery == "negative_sign":
        item = item.model_copy(update={
            "amount": "-100.00",
            "amount_excerpt": "Closing balance GBP 100.00 as of 2026-08-31; account ending 1234.",
        })
    elif forgery == "currency_binding":
        item = item.model_copy(update={
            "amount": "200.00",
            "currency": "HKD",
            "amount_excerpt": (
                "Closing balances HKD 1,000.00 and USD 200.00 as of 2026-08-31; "
                "account ending 1234."
            ),
        })
    elif forgery == "subject":
        item = item.model_copy(update={"subject_name": "Invented Example Holder"})
    elif forgery == "date":
        item = item.model_copy(update={"as_of": date(2026, 9, 1)})
    else:
        document_kind = "sponsor_funds"
        holder = SPONSOR
        item = observation(subject=SPONSOR, amount="9000.00", account="account ending 9876")
        include_name_fact = True
    proposal = financial_proposal(
        f"North Harbour forged proposal {forgery}",
        document_kind=cast(DocumentKind, document_kind),
        item=item,
        holder=holder,
        include_name_fact=include_name_fact,
    )
    # The PDF contains the genuine base locators when the model proposed a
    # fabricated locator/value. Classification and any legitimate name line are
    # still present, so only the financial grounding boundary causes the hold.
    if forgery in {"subject_excerpt", "amount_excerpt", "amount_boundary", "negative_sign",
                   "currency_binding", "subject", "date"}:
        genuine = observation()
        lines = [proposal.classification_excerpt, f"Account holder {APPLICANT}",
                 genuine.subject_excerpt]
        if forgery == "amount_boundary":
            lines.append("Closing balance GBP 5,100 as of 2026-08-31; account ending 1234.")
        elif forgery == "negative_sign":
            lines.append("Closing balance GBP 100.00 as of 2026-08-31; account ending 1234.")
        elif forgery == "currency_binding":
            lines.append(item.amount_excerpt)
        else:
            lines.append(genuine.amount_excerpt)
        path = plain_pdf(tmp_path / f"forged-{forgery}.pdf", *dict.fromkeys(lines))
    else:
        path = plain_pdf(tmp_path / f"forged-{forgery}.pdf", *document_lines(proposal))

    result = journey.turn(
        "Please retain this document for review.",
        documents=[(path, proposal)],
    )

    document = result.case.documents[-1]
    assert document.kind == "unknown"
    assert document.status == DocumentStatus.HUMAN_REVIEW_REQUIRED
    assert not any(item.fact_key == "financial_observation"
                   and item.source_document_id == document.id for item in result.case.evidence)
    assert_financial_profile_unchanged(result.case)
