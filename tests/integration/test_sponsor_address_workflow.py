"""Captured fictional workflow: supplied address, persistence and identity changes."""

import pytest
from test_consultant_value import Conversation, _patch
from test_next_step_workflow import _seed

from visa_agent.domain.rules import profile_fact_complete, required_profile_facts
from visa_agent.storage.sqlite import SQLiteStore


def with_address(tmp_path):
    dialogue = Conversation(tmp_path)
    body = "My mother is paying for my trip."
    dialogue.turn(body, _patch(updates=[("funding_source", "personal_sponsor", body),
                                      ("sponsor_relationship", "mother", body)]))
    body = "My sponsor's address is 12 Example Road, Hong Kong."
    result = dialogue.turn(body, _patch(updates=[("sponsor_address", "12 Example Road, Hong Kong", body)]))
    return dialogue, result


def test_sponsor_address_survives_reopen_with_exact_source_and_never_becomes_home(tmp_path):
    dialogue, supplied = with_address(tmp_path)
    assert supplied.case.profile.sponsor_address == "12 Example Road, Hong Kong"
    assert supplied.case.profile.current_address is None
    evidence = supplied.case.active_evidence("sponsor_address")
    assert len(evidence) == 1 and evidence[0].source_event_id == supplied.event.id
    later = dialogue.turn("Thanks.", _patch())
    assert later.case.profile.sponsor_address == supplied.case.profile.sponsor_address


@pytest.mark.parametrize("change", ["relationship", "self_funded"])
def test_previous_persons_address_cannot_follow_funding_or_identity_change(tmp_path, change):
    dialogue, _ = with_address(tmp_path)
    if change == "relationship":
        body = "My father is paying for my trip."
        updates = [("sponsor_relationship", "father", body)]
    else:
        body = "I am paying for my trip myself."
        updates = [("funding_source", "self", body)]
    changed = dialogue.turn(body, _patch(updates=updates))
    assert changed.case.profile.sponsor_address is None
    assert not changed.case.active_evidence("sponsor_address")
    assert any(item.fact_key == "sponsor_address" and item.superseded for item in changed.case.evidence)


def test_same_event_replacement_can_supply_new_persons_explicit_address(tmp_path):
    dialogue, _ = with_address(tmp_path)
    relation = "My father is paying for my trip."
    address = "My sponsor's address is 34 Another Road, Hong Kong."
    changed = dialogue.turn(f"{relation} {address}", _patch(updates=[
        ("sponsor_relationship", "father", relation),
        ("sponsor_address", "34 Another Road, Hong Kong", address),
    ]))
    assert changed.case.profile.sponsor_address == "34 Another Road, Hong Kong"
    assert len(changed.case.active_evidence("sponsor_address")) == 1
    assert changed.case.active_evidence("sponsor_address")[0].source_event_id == changed.event.id


def asking(tmp_path):
    dialogue = Conversation(tmp_path)
    first = dialogue.turn("Hello.", _patch())
    store = SQLiteStore(dialogue.path)
    try:
        case = store.get_case(first.case.id)
        seed = _seed()
        case.profile = seed.profile
        case.deferred_fields = seed.deferred_fields
        case.profile.funding_source = "personal_sponsor"
        case.profile.sponsor_name = "Fictional Parent"
        case.profile.sponsor_relationship = "mother"
        case.profile.sponsor_is_in_uk = False
        case.profile.sponsor_address = None
        store.save_case(case)
    finally:
        store.close()
    asked = dialogue.turn("Please help me complete my application information.", _patch())
    assert asked.case.last_requested_fields == ["sponsor_address"], asked.body
    return dialogue, asked


def test_sent_address_question_accepts_literal_short_answer_without_reasking(tmp_path):
    dialogue, asked = asking(tmp_path)
    assert "sponsor_address" in required_profile_facts(asked.case)
    assert not profile_fact_complete(asked.case, "sponsor_address")
    body = "12 Example Road, Hong Kong"
    result = dialogue.turn(body, _patch(updates=[("sponsor_address", body, body)]))
    assert result.case.profile.sponsor_address == body
    assert f"your sponsor's address is {body}" in result.body
    assert "sponsor_address" not in result.case.last_requested_fields
    assert profile_fact_complete(result.case, "sponsor_address")
    assert not result.case.final_summary_confirmed and result.case.delivery_path is None


def test_uncertainty_is_preserved_but_stays_incomplete_until_supplied(tmp_path):
    dialogue, asked = asking(tmp_path)
    deferred = dialogue.turn("I need to check.", _patch())
    assert "sponsor_address" in deferred.case.deferred_fields
    assert deferred.case.sponsor_address_deferrals[-1]["question_event_id"] == asked.event.id
    assert not profile_fact_complete(deferred.case, "sponsor_address")
    assert "Ask them when convenient" in deferred.body
    later = dialogue.turn("Thanks.", _patch())
    assert "sponsor_address" in later.case.deferred_fields
    assert "What is your sponsor's full current address?" not in later.body
    body = "My sponsor's address is 12 Example Road, Hong Kong."
    supplied = dialogue.turn(body, _patch(updates=[("sponsor_address", "12 Example Road, Hong Kong", body)]))
    assert supplied.case.profile.sponsor_address == "12 Example Road, Hong Kong"
    assert "sponsor_address" not in supplied.case.deferred_fields


@pytest.mark.parametrize("invalid", ["unsent", "future", "other_sponsor"])
@pytest.mark.parametrize("body", ["12 Example Road, Hong Kong", "I need to check."])
def test_unverified_question_cannot_attribute_short_address_or_uncertainty(tmp_path, invalid, body):
    dialogue, asked = asking(tmp_path)
    store = SQLiteStore(dialogue.path)
    try:
        if invalid == "other_sponsor":
            case = store.get_case(asked.case.id)
            case.sponsor_address_question_identity = "different-sponsor"
            store.save_case(case)
        else:
            sql = ("UPDATE outbox SET status='PENDING' WHERE case_id=? AND event_id=?" if invalid == "unsent"
                   else "UPDATE outbox SET sent_at='2026-09-04T10:04:00+00:00' WHERE case_id=? AND event_id=?")
            store.connection.execute(sql, (asked.case.id, asked.event.id))
            store.connection.commit()
    finally:
        store.close()
    updates = [("sponsor_address", body, body)] if body.startswith("12") else []
    result = dialogue.turn(body, _patch(updates=updates))
    assert result.case.profile.sponsor_address is None
    assert not result.case.sponsor_address_deferrals
    assert not result.model.events[0].known_profile["_sponsor_address_question_verified"]


def test_changing_sponsor_clears_previous_deferral_but_retains_history(tmp_path):
    dialogue, _ = asking(tmp_path)
    dialogue.turn("I need to check.", _patch())
    body = "My father is paying for my trip."
    changed = dialogue.turn(body, _patch(updates=[("sponsor_relationship", "father", body)]))
    assert "sponsor_address" not in changed.case.deferred_fields
    assert changed.case.sponsor_address_question_identity is None
    assert len(changed.case.sponsor_address_deferrals) == 1
