"""Customer-directed statement corrections; not approval or deletion of evidence."""

from __future__ import annotations

import re
from email.utils import getaddresses
from uuid import NAMESPACE_URL, uuid5

from visa_agent.domain.models import Case, DocumentStatus, Evidence, InboundEvent, ProvenanceState
from visa_agent.workflow.advice_preferences import _current_clauses

_FILE = r"[\w -]{1,150}\.pdf"


def replacement_names(body: str) -> tuple[str, str] | None:
    """One current, explicit pair; quotations/conditions are not commands."""
    from visa_agent.workflow.conversation import latest_reply_text

    body = latest_reply_text(body)
    if len(body) > 6000 or re.search(
        r"如果|假如|假设|等我|确认后|转述|写道|示例|模板|说[:：]|"
        r"\b(?:if|unless|hypothetically|said|wrote|example|template)\b", body, re.I,
    ):
        return None
    # Gmail inserts soft line breaks around filenames. Preserve history stripping
    # and conditional/report boundaries before joining those transport line breaks.
    body = re.sub(r"\s*\n\s*", " ", body)
    matches: list[tuple[str, str]] = []
    clauses = _current_clauses(body)
    if any(re.search(r"(?:不|别|取消|暂缓).{0,16}(?:操作|执行|替换|更换)|"
                     r"\b(?:don't|do not|cancel|wait|hold off)\b.{0,25}(?:replace|replacement|proceed|act)",
                     clause, re.I) for clause in clauses):
        return None
    for clause in clauses:
        # A later refusal or condition must not be split away from an imperative.
        if re.search(r"不|别|暂缓|取消|等我|确认后|\b(?:not|don't|never|cancel|wait|unless|if)\b",
                     clause, re.I):
            continue
        for pattern in (
            rf"请用\s*(?P<new>{_FILE})\s*替换(?:之前的|原来的|旧的)?\s*(?P<old>{_FILE})",
            rf"附件是(?:更正后的|修正后的)?\s*(?P<new>{_FILE})\s*[,，]\s*请用它替换(?:之前的|原来的|旧的)?\s*(?P<old>{_FILE})",
            rf"please replace\s+(?P<old>{_FILE})\s+with\s+(?P<new>{_FILE})",
        ):
            match = re.match(pattern + r"(?=$|[,，;；])", clause.strip(), re.I)
            if match:
                matches.append((match['new'].strip(), match['old'].strip()))
    return matches[0] if len(matches) == 1 and matches[0][0] != matches[0][1] else None


def apply_statement_replacement(case: Case, event: InboundEvent) -> bool:
    contacts = [getaddresses([value]) for value in (case.applicant_contact, event.sender)]
    if (case.external_thread_id != event.external_thread_id or case.primary_channel != event.channel
            or any(len(items) != 1 or not items[0][1] for items in contacts)
            or contacts[0][0][1].casefold() != contacts[1][0][1].casefold()):
        return False
    pair = replacement_names(event.body)
    if pair is None:
        return False
    new_name, old_name = pair
    active = [doc for doc in case.documents if doc.status == DocumentStatus.ACCEPTED_FOR_REVIEW]
    new_docs = [doc for doc in active if doc.filename == new_name]
    old_docs = [doc for doc in active if doc.filename == old_name]
    if len(new_docs) != 1 or len(old_docs) != 1:
        return False
    new, old = new_docs[0], old_docs[0]
    if (new.kind not in {"bank_statement", "sponsor_funds"} or old.kind != new.kind
            or new.supersedes_document_id is not None or old.sha256 == new.sha256):
        return False
    observations = {}
    for doc in (new, old):
        items = [ev.value for ev in case.active_evidence("financial_observation")
                 if ev.source_document_id == doc.id and isinstance(ev.value, dict)
                 and ev.provenance_state not in {ProvenanceState.STALE, ProvenanceState.INSUFFICIENT,
                                                 ProvenanceState.UNAVAILABLE}]
        if len(items) != 1:
            return False
        observations[doc.id] = items[0]
    current, previous = observations[new.id], observations[old.id]
    # This path corrects the holder spelling on the SAME account and statement.
    # A new period/account, unknown account, or changed balance needs separate handling.
    keys = ("kind", "currency", "period", "as_of", "account_reference", "amount")
    if any(not current.get(key) or current.get(key) != previous.get(key) for key in keys):
        return False
    expected = case.profile.sponsor_name if new.kind == "sponsor_funds" else case.profile.full_name
    if not expected or str(current.get("subject_name", "")).strip().casefold() != expected.strip().casefold():
        return False
    # Never replace a document whose new evidence has an independent open blocker.
    if any(new.id in issue.related_document_ids for issue in case.open_blockers()):
        return False
    old.status = DocumentStatus.SUPERSEDED
    new.supersedes_document_id = old.id
    for evidence in case.evidence:
        if evidence.source_document_id == old.id:
            evidence.superseded = True
    case.evidence.append(Evidence(
        id="replacement-" + uuid5(NAMESPACE_URL, f"{event.id}:{old.id}:{new.id}").hex,
        fact_key="document_replacement", value={"old_document_id": old.id, "new_document_id": new.id},
        source_event_id=event.id, source_excerpt=event.body,
        extraction_method="explicit_customer_statement_replacement", model_version="none", confidence=1,
        provenance_state=ProvenanceState.EXTRACTED_UNVERIFIED,
    ))
    return True
