"""Synthetic inbound state/provenance tests; not live-model or release coverage."""

from test_consultant_value import Conversation, _patch


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
