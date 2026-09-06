"""Fictional operator actions: no login, live mail, legal or truth approval claim."""

from datetime import date

import pytest
from test_application_record_workflow import patch, record
from test_consultant_value import POLICY, Conversation

from visa_agent.domain.record_review import RecordAssessment, record_review_is_current
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.application_records import CollectionDeclarationProposal
from visa_agent.privacy.consent import ConsentLedger, ProcessingScope
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.record_review import review_application_records
from visa_agent.workflow.review import review_fingerprint


def setup(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "I visited Japan in May 2023 for tourism. This is my full travel history. I have no UK contacts."
    turn = dialogue.turn(body, patch(
        records=[record("I visited Japan in May 2023 for tourism.", {"country": "Japan", "period": "May 2023", "purpose": "tourism"})],
        assertions=[
            CollectionDeclarationProposal(kind="travel", state="complete_declared", source_excerpt="This is my full travel history.", confidence=1),
            CollectionDeclarationProposal(kind="uk_contact", state="none_declared", source_excerpt="I have no UK contacts.", confidence=1),
        ],
    ))
    store = SQLiteStore(dialogue.path)
    assessment = RecordAssessment(record_id=next(iter(turn.case.application_records.current())),
        relationship_category="not_a_contact", applicable_details_checked=True,
        rationale="Fictional operator checked the supplied period and travel details for this scenario.")
    kwargs = dict(case_id=turn.case.id, expected_fingerprint=review_fingerprint(turn.case), policy=POLICY,
                  actor="Fictional reviewer", rationale="Checked the fictional case's record scope and retained evidence.",
                  assessments=[assessment], travel_history_scope_checked=True, today=date(2026, 9, 7))
    return store, turn.case, kwargs


def test_review_persists_with_history_without_confirming_or_sending(tmp_path):
    store, case, kwargs = setup(tmp_path)
    try:
        before_outbox = store.list_outbox()
        review = review_application_records(store, **kwargs)
        saved = store.get_case(case.id)
        assert saved.application_record_review == review
        assert saved.application_record_review_history == [review]
        assert record_review_is_current(saved, POLICY)
        gate = evaluate_gate(saved, POLICY, kwargs["today"])
        assert gate.checks["application_record_intake_release_checked"]
        assert not gate.allowed  # record review alone is not a complete application
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert saved.delivery_path is None and saved.confirmation_kind is None
        assert store.list_outbox() == before_outbox
        saved.profile.estimated_trip_cost_gbp = 1234
        assert not record_review_is_current(saved, POLICY)
        changed_policy = POLICY.model_copy(deep=True)
        changed_policy.disclaimer += " Changed policy content."
        assert not record_review_is_current(store.get_case(case.id), changed_policy)
    finally:
        store.close()


@pytest.mark.parametrize("relationship,category,allowed", [
    ("sister", "family", False), ("sister", "other_contact", False), ("friend", "other_contact", True),
])
def test_family_passport_requirement_is_not_a_blanket_friend_requirement(tmp_path, relationship, category, allowed):
    dialogue = Conversation(tmp_path)
    statement = f"My {relationship} Example Doe lives at 1 Example Road, London, UK."
    turn = dialogue.turn(statement + " My UK contacts list is complete. I have no travel history.", patch(
        records=[record(statement, {"name": "Example Doe", "relationship": relationship,
                                   "address": "1 Example Road, London, UK"}, kind="uk_contact")],
        assertions=[
            CollectionDeclarationProposal(kind="uk_contact", state="complete_declared", source_excerpt="My UK contacts list is complete.", confidence=1),
            CollectionDeclarationProposal(kind="travel", state="none_declared", source_excerpt="I have no travel history.", confidence=1),
        ],
    ))
    store = SQLiteStore(dialogue.path)
    try:
        kwargs = dict(case_id=turn.case.id, expected_fingerprint=review_fingerprint(turn.case), policy=POLICY,
                      actor="Fictional reviewer", rationale="Assessed the fictional contact relationship and applicable details.",
                      assessments=[RecordAssessment(record_id=next(iter(turn.case.application_records.current())),
                          relationship_category=category, applicable_details_checked=True,
                          rationale="Checked the stated relationship against the supplied contact details.")],
                      travel_history_scope_checked=False, today=date(2026, 9, 7))
        if allowed:
            review_application_records(store, **kwargs)
            assert record_review_is_current(store.get_case(turn.case.id), POLICY)
        else:
            with pytest.raises(ValueError):
                review_application_records(store, **kwargs)
            assert store.get_case(turn.case.id).application_record_review is None
    finally:
        store.close()


@pytest.mark.parametrize("failure", ["stale", "missing_assessment", "duplicate", "unchecked", "scope", "source", "paused", "expired", "ungranted"])
def test_failed_review_is_atomic_and_cannot_change_case_or_outbox(tmp_path, failure):
    store, case, kwargs = setup(tmp_path)
    try:
        if failure == "stale":
            kwargs["expected_fingerprint"] = "stale"
        elif failure == "missing_assessment":
            kwargs["assessments"] = []
        elif failure == "duplicate":
            kwargs["assessments"] *= 2
        elif failure == "unchecked":
            kwargs["assessments"] = [kwargs["assessments"][0].model_copy(update={"applicable_details_checked": False})]
        elif failure == "scope":
            kwargs["travel_history_scope_checked"] = False
        elif failure == "source":
            store.connection.execute("DELETE FROM processed_events WHERE case_id=?", (case.id,))
        elif failure == "paused":
            case.preparation_paused = True
            case.preparation_control_epoch += 1
            store.save_case(case)
            kwargs["expected_fingerprint"] = review_fingerprint(case)
        elif failure == "ungranted":
            ConsentLedger(store).configure(ProcessingScope(provider="fictional-provider", model="fictional-model"))
        else:
            kwargs["today"] = date(2030, 1, 1)
        before = store.get_case(case.id).model_dump_json()
        outbox = store.list_outbox()
        with pytest.raises(ValueError):
            review_application_records(store, **kwargs)
        assert store.get_case(case.id).model_dump_json() == before
        assert store.list_outbox() == outbox
    finally:
        store.close()


def test_reviewed_trip_requires_fresh_sent_confirmations_before_zip(tmp_path):
    from datetime import UTC, datetime, timedelta

    from test_conversation_confirmation import setup_case

    from visa_agent.channels.outbound import OutboxDispatcher
    from visa_agent.delivery.pack import generate_pack
    from visa_agent.llm.guarded import GuardedLLM
    from visa_agent.llm.offline import OfflineFixtureLLM

    store, workflow, case, template = setup_case(tmp_path, "gmail", stop_at_profile=True)
    statement = "I visited Japan in May 2023 for tourism. This is my full travel history."

    class RecordModel(OfflineFixtureLLM):
        def extract_case_patch(self, event):
            if event.body == statement:
                return patch(records=[record("I visited Japan in May 2023 for tourism.", {
                    "country": "Japan", "period": "May 2023", "purpose": "tourism",
                })], assertions=[CollectionDeclarationProposal(kind="travel", state="complete_declared",
                    source_excerpt="This is my full travel history.", confidence=1)])
            return super().extract_case_patch(event)

    class Capture:
        def send(self, request):
            return "fictional-captured-" + request.outbox_id

    def turn(number, body):
        return workflow.process(template.model_copy(update={
            "id": f"record-review-journey-{number}", "body": body,
            "received_at": template.received_at + timedelta(minutes=number),
        }))

    try:
        workflow.llm = GuardedLLM(RecordModel())
        case, _, _ = turn(1, statement)
        assert not evaluate_gate(case, POLICY, date(2026, 9, 4)).checks["application_record_intake_release_checked"]
        review_application_records(store, case_id=case.id, expected_fingerprint=review_fingerprint(case),
            policy=POLICY, actor="Fictional reviewer", rationale="Checked fictional record applicability and registered sources.",
            assessments=[RecordAssessment(record_id=next(iter(case.application_records.current())),
                relationship_category="not_a_contact", applicable_details_checked=True,
                rationale="Checked the supplied past-trip period and applicable history scope.")],
            travel_history_scope_checked=True, today=date(2026, 9, 4))
        reviewed = store.get_case(case.id)
        archive, _ = generate_pack(reviewed, POLICY, store, tmp_path / "reviewed-pack", date(2026, 9, 4))
        assert archive is None and not reviewed.profile_confirmed and not reviewed.final_summary_confirmed
        case, _, plan = turn(2, "Please continue preparing my application.")
        assert plan == "awaiting_profile_confirmation"
        OutboxDispatcher(store, Capture(), allowed_message_types=("awaiting_profile_confirmation",)).dispatch_due(datetime.now(UTC))
        case, _, plan = turn(3, "I confirm the profile summary")
        assert plan == "awaiting_confirmation" and record_review_is_current(case, POLICY)
        OutboxDispatcher(store, Capture(), allowed_message_types=("awaiting_confirmation",)).dispatch_due(datetime.now(UTC))
        case, _, plan = turn(4, "I CONFIRM THE FINAL SUMMARY")
        assert plan == "ready" and record_review_is_current(case, POLICY)
        archive, reasons = generate_pack(case, POLICY, store, tmp_path / "reviewed-pack", date(2026, 9, 4))
        assert archive is not None and archive.is_file() and not reasons
        store.connection.execute("DELETE FROM processed_events WHERE event_id=?", ("record-review-journey-1",))
        recovered, reasons = generate_pack(case, POLICY, store, tmp_path / "reviewed-pack", date(2026, 9, 4))
        assert recovered is None and any("source registration" in reason for reason in reasons)
        assert archive.is_file()  # refusal does not destroy an existing artifact
    finally:
        store.close()
