"""Explicit synthetic record declarations for pre-populated scenario fixtures.

Not a production migration or default. Call only in fixtures whose scenario
assumes completed intake; new/legacy missing-intake tests must not call this.
This runs source/ownership planning but does not claim an actual mailbox event.
"""

from datetime import UTC, datetime

from visa_agent.domain.models import Case, InboundEvent
from visa_agent.llm.application_records import CollectionDeclarationProposal
from visa_agent.workflow.record_intake import plan_record_intake


def with_explicit_no_record_fixture(case: Case) -> Case:
    event = InboundEvent(
        id=f"{case.id}-synthetic-record-declarations", channel=case.primary_channel,
        external_thread_id=case.external_thread_id, sender=case.applicant_contact,
        subject="Explicit synthetic completed-intake fixture",
        body="I have no travel history. I have no UK contacts.",
        received_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    plan = plan_record_intake(event, None, case_id=case.id, records=[], declarations=[
        CollectionDeclarationProposal(kind="travel", state="none_declared", source_excerpt="I have no travel history.", confidence=1),
        CollectionDeclarationProposal(kind="uk_contact", state="none_declared", source_excerpt="I have no UK contacts.", confidence=1),
    ])
    assert plan.changed and not plan.requires_review
    result = case.model_copy(deep=True)
    result.application_records = plan.ledger
    return result
