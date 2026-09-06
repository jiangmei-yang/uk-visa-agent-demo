"""Synthetic inbound state/provenance tests; not live-model or release coverage."""

import pytest
from test_consultant_value import Conversation, _patch
from test_next_step_workflow import _seed

from visa_agent.domain.rules import profile_fact_complete, required_profile_facts
from visa_agent.storage.sqlite import SQLiteStore


def test_current_home_duration_survives_reopen_and_does_not_follow_a_new_address(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "My current home address is 1 Example Road, Hong Kong. I've lived at my current address for about two years."
    first = dialogue.turn(body, _patch(updates=[
        ("current_address", "1 Example Road, Hong Kong", "My current home address is 1 Example Road, Hong Kong."),
        ("current_address_duration", "about two years", "I've lived at my current address for about two years."),
    ]))
    assert first.case.profile.current_address_duration == "about two years"
    assert first.case.active_evidence("current_address_duration")[0].source_event_id == first.event.id
    later = dialogue.turn("Thanks.", _patch())
    assert later.case.profile.current_address_duration == "about two years"
    changed_body = "My current home address is 2 Example Road, Hong Kong."
    changed = dialogue.turn(changed_body, _patch(updates=[("current_address", "2 Example Road, Hong Kong", changed_body)]))
    assert changed.case.profile.current_address_duration is None
    assert not changed.case.active_evidence("current_address_duration")
    assert any(item.fact_key == "current_address_duration" and item.superseded for item in changed.case.evidence)


def asking(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn("Hello.", _patch())
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        # Explicitly populated unrelated scalar fixture, never used as evidence
        # for the duration: that missing fact must be asked/supplied below.
        seed = _seed()
        case.profile = seed.profile
        case.profile.current_address_duration = None  # deliberately incomplete for the actual question/answer test
        case.deferred_fields = seed.deferred_fields
        store.save_case(case)
    finally:
        store.close()
    asked = dialogue.turn("Please help me complete my application information.", _patch())
    assert asked.case.last_requested_fields == ["current_address_duration"], asked.body
    return dialogue, asked


def test_sent_duration_question_accepts_short_answer_and_never_bypasses_the_gate(tmp_path):
    dialogue, asked = asking(tmp_path)
    assert "current_address_duration" in required_profile_facts(asked.case)
    assert not profile_fact_complete(asked.case, "current_address_duration")
    body = "about two years"
    answered = dialogue.turn(body, _patch(updates=[("current_address_duration", body, body)]))
    assert answered.case.profile.current_address_duration == body
    assert "current_address_duration" not in answered.case.last_requested_fields
    assert not answered.case.final_summary_confirmed and answered.case.delivery_path is None


def test_sent_uncertainty_is_preserved_without_becoming_a_duration_or_repeated_question(tmp_path):
    dialogue, asked = asking(tmp_path)
    deferred = dialogue.turn("I need to check.", _patch())
    assert "current_address_duration" in deferred.case.deferred_fields
    assert deferred.case.residence_duration_deferrals[-1]["question_event_id"] == asked.event.id
    assert not profile_fact_complete(deferred.case, "current_address_duration")
    assert "Don't guess" in deferred.body
    later = dialogue.turn("Thanks, I will look for my tenancy agreement.", _patch())
    assert "current_address_duration" in later.case.deferred_fields
    assert "About how long have you lived" not in later.body


@pytest.mark.parametrize("invalid", ["unsent", "different_address"])
def test_a_question_hint_without_matching_sent_current_home_context_cannot_ground_a_short_answer(tmp_path, invalid):
    dialogue, asked = asking(tmp_path)
    store = SQLiteStore(dialogue.path)
    try:
        if invalid == "unsent":
            store.connection.execute("UPDATE outbox SET status='PENDING' WHERE case_id=? AND event_id=?",
                                     (asked.case.id, asked.event.id))
            store.connection.commit()
        else:
            case = store.get_case(asked.case.id)
            case.residence_duration_question_address = "Different former home"
            store.save_case(case)
    finally:
        store.close()
    body = "about two years"
    replied = dialogue.turn(body, _patch(updates=[("current_address_duration", body, body)]))
    assert replied.case.profile.current_address_duration is None
    assert not replied.model.events[0].known_profile["_residence_duration_question_verified"]
