"""Captured fictional workflow: supplied address, persistence and identity changes."""

import pytest
from test_consultant_value import Conversation, _patch


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
