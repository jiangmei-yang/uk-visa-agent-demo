"""Comparable financial observations, never funding sufficiency or conversion.

The document reader already grounds each retained observation. This module only
compares active observations from accepted documents on an identical printed
currency, period, basis, date, subject and non-empty account reference. It does
not update profile amounts, sum accounts, convert currencies or set a threshold.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from visa_agent.domain.financial_evidence import financial_fields_are_coherent
from visa_agent.domain.models import (
    Case,
    DocumentStatus,
    Evidence,
    Issue,
    IssueSeverity,
    ProvenanceState,
)

_AMOUNT = re.compile(r"^-?(?:0|[1-9][0-9]{0,12})(?:\.\d{1,2})?$")
_CURRENCIES = {"GBP", "CNY", "HKD", "USD", "EUR"}
_PERIODS = {"annual", "monthly", "closing"}
_BASES = {"gross", "net", "unspecified"}
_REQUIRED = {"kind", "subject_name", "amount", "currency", "period", "basis", "as_of",
             "subject_page", "subject_excerpt", "date_page", "date_excerpt"}
_ALLOWED = _REQUIRED | {"account_reference", "account_page", "account_excerpt"}


def _canonical(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class _Observation:
    evidence: Evidence
    document_id: str
    document_kind: str
    kind: str
    subject: str
    amount: Decimal
    currency: str
    period: str
    basis: str
    as_of: str
    account_reference: str | None

    @property
    def comparison_key(self) -> tuple[str, ...] | None:
        if not self.account_reference:
            return None
        return (self.kind, self.subject, self.currency, self.period, self.basis,
                self.as_of, self.account_reference)


def _parse(evidence: Evidence, document_kind: str) -> _Observation | None:
    if (
        evidence.fact_key != "financial_observation"
        or evidence.superseded
        or evidence.provenance_state
        in {ProvenanceState.STALE, ProvenanceState.INSUFFICIENT, ProvenanceState.UNAVAILABLE}
        or evidence.source_document_id is None
        or evidence.confidence < 0.95
        or not isinstance(evidence.page, int)
        or evidence.page < 1
        or not evidence.source_excerpt.strip()
        or len(evidence.source_excerpt) > 600
    ):
        return None
    value: Any = evidence.value
    if not isinstance(value, dict) or set(value) - _ALLOWED or not set(value) >= _REQUIRED:
        return None
    kind, subject, amount = value.get("kind"), value.get("subject_name"), value.get("amount")
    currency, period, basis, as_of = (value.get("currency"), value.get("period"),
                                      value.get("basis"), value.get("as_of"))
    account = value.get("account_reference")
    subject_page, subject_excerpt = value.get("subject_page"), value.get("subject_excerpt")
    date_page, date_excerpt = value.get("date_page"), value.get("date_excerpt")
    account_page, account_excerpt = value.get("account_page"), value.get("account_excerpt")
    if (kind not in {"salary", "closing_balance"} or not isinstance(subject, str)
            or not _canonical(subject) or len(subject) > 160 or not isinstance(amount, str)
            or not _AMOUNT.fullmatch(amount) or currency not in _CURRENCIES
            or period not in _PERIODS or basis not in _BASES or not isinstance(as_of, str)
            or not isinstance(subject_page, int) or subject_page < 1
            or not isinstance(subject_excerpt, str) or not subject_excerpt.strip()
            or len(subject_excerpt) > 600 or not isinstance(date_page, int) or date_page < 1
            or not isinstance(date_excerpt, str) or not date_excerpt.strip()
            or len(date_excerpt) > 600):
        return None
    try:
        if date.fromisoformat(as_of).isoformat() != as_of:
            return None
        numeric = Decimal(amount)
    except (ValueError, InvalidOperation):
        return None
    if account is not None and (not isinstance(account, str)
            or not 2 <= len(account) <= 80 or not _canonical(account)
            or not isinstance(account_page, int) or account_page < 1
            or not isinstance(account_excerpt, str) or not account_excerpt.strip()
            or len(account_excerpt) > 600):
        return None
    if account is None and (account_page is not None or account_excerpt is not None):
        return None
    permitted = {
        "employment_letter": ("salary", {"annual", "monthly"}),
        "bank_statement": ("closing_balance", {"closing"}),
        "sponsor_funds": ("closing_balance", {"closing"}),
    }.get(document_kind)
    if permitted is None or kind != permitted[0] or period not in permitted[1]:
        return None
    if kind == "closing_balance" and basis != "unspecified":
        return None
    if kind == "salary" and account is not None:
        return None
    if not financial_fields_are_coherent(
        kind=kind,
        subject_name=subject,
        amount=amount,
        currency=currency,
        period=period,
        basis=basis,
        as_of=as_of,
        account_reference=account,
        subject_excerpt=subject_excerpt,
        amount_excerpt=evidence.source_excerpt,
        date_excerpt=date_excerpt,
        account_excerpt=account_excerpt,
    ):
        return None
    return _Observation(evidence, evidence.source_document_id, document_kind, kind,
        _canonical(subject), numeric, currency, period, basis, as_of,
        _canonical(account) if account is not None else None)


def financial_observation_is_valid(evidence: Evidence, document_kind: str) -> bool:
    """Revalidate serialized evidence before it can satisfy a delivery requirement."""
    return _parse(evidence, document_kind) is not None


def _put_issue(case: Case, issue: Issue) -> None:
    # Import lazily so rules can call this module without an import cycle.
    from visa_agent.domain.rules import _upsert_issue

    _upsert_issue(case, issue)
    current = next(item for item in case.issues if item.code == issue.code)
    if current.status == "OPEN":
        current.title = issue.title
        current.detail = issue.detail
        current.severity = issue.severity
        current.related_document_ids = issue.related_document_ids


def _resolve(case: Case, code: str, resolution: str) -> None:
    from visa_agent.domain.rules import resolve_issue

    resolve_issue(case, code, resolution)


def _group_code(key: tuple[str, ...]) -> str:
    encoded = json.dumps(key, ensure_ascii=False, separators=(",", ":"))
    return "FINANCIAL_OBSERVATION_CONFLICT_" + hashlib.sha256(encoded.encode()).hexdigest()[:12]


def apply_financial_consistency_checks(case: Case) -> None:
    """Mutate only financial issues on ``case`` from currently comparable evidence."""
    accepted = {document.id: document for document in case.documents
                if document.status == DocumentStatus.ACCEPTED_FOR_REVIEW}
    observations: list[_Observation] = []
    for evidence in case.evidence:
        if (evidence.fact_key != "financial_observation" or evidence.superseded
                or evidence.provenance_state in {
                    ProvenanceState.STALE, ProvenanceState.INSUFFICIENT, ProvenanceState.UNAVAILABLE,
                } or evidence.source_document_id not in accepted):
            continue
        document = accepted[evidence.source_document_id]
        parsed = _parse(evidence, document.kind)
        if parsed is not None:
            observations.append(parsed)

    by_document: dict[str, list[_Observation]] = {}
    for item in observations:
        by_document.setdefault(item.document_id, []).append(item)
    active_owner_codes: set[str] = set()
    for document_id, items in by_document.items():
        document_kind = items[0].document_kind
        expected = (case.profile.sponsor_name if document_kind == "sponsor_funds"
                    else case.profile.full_name if document_kind in {"bank_statement", "employment_letter"}
                    else None)
        if not expected:
            continue
        code = f"FINANCIAL_OWNER_MISMATCH_{document_id}"
        active_owner_codes.add(code)
        if any(item.subject != _canonical(expected) for item in items):
            owner = "sponsor" if document_kind == "sponsor_funds" else "applicant"
            _put_issue(case, Issue(id=f"issue-{case.id}-financial-owner-{document_id}", code=code,
                title="Financial document owner needs checking",
                detail=(f"The financial subject printed in document {document_id} does not exactly match the "
                        f"{owner} name currently on file. Check the document owner and recorded name; this is an "
                        "identity consistency issue that needs adviser review."),
                severity=IssueSeverity.BLOCKER, related_document_ids=[document_id]))
        else:
            _resolve(case, code, "The accepted document's financial subject matches the current name on file.")

    groups: dict[tuple[str, ...], list[_Observation]] = {}
    for item in observations:
        key = item.comparison_key
        if item.kind == "salary":
            key = (item.kind, item.subject, item.currency, item.period, item.basis,
                   item.as_of, "not_applicable")
        if key is not None:
            groups.setdefault(key, []).append(item)
    active_group_codes: set[str] = set()
    for key, items in groups.items():
        code = _group_code(key)
        active_group_codes.add(code)
        conflict = any(left.amount != right.amount and left.document_id != right.document_id
                       for index, left in enumerate(items) for right in items[index + 1:])
        if conflict:
            document_ids = sorted({item.document_id for item in items})
            kind, subject, currency, period, basis, as_of, account = key
            comparison = ("the same salary date and basis" if kind == "salary" else
                          "the same recorded account reference")
            _put_issue(case, Issue(id=f"issue-{case.id}-financial-conflict-{code[-12:]}", code=code,
                title="Comparable financial amounts need checking",
                detail=(f"Accepted documents {', '.join(document_ids)} contain different {kind} amounts for "
                        f"the same recorded subject, currency {currency}, period {period}, basis {basis}, "
                        f"as of {as_of} and {comparison}. Check the source values and context; "
                        "this is a consistency review, not a funding-sufficiency decision."),
                severity=IssueSeverity.BLOCKER, related_document_ids=document_ids))
        elif len({item.document_id for item in items}) >= 2:
            _resolve(case, code, "The currently comparable accepted observations show one amount.")
        else:
            _resolve(case, code, "There are no longer two current comparable financial sources for this issue.")

    for issue in list(case.issues):
        if issue.status != "OPEN":
            continue
        if issue.code.startswith("FINANCIAL_OWNER_MISMATCH_") and issue.code not in active_owner_codes:
            _resolve(case, issue.code, "There is no longer a current accepted observation for this owner check.")
        elif (issue.code.startswith("FINANCIAL_OBSERVATION_CONFLICT_")
              and issue.code not in active_group_codes):
            _resolve(case, issue.code,
                     "The previous sources are no longer current and comparable on the same recorded basis.")
