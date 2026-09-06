"""Fictional current-employer persistence and old-employer invalidation."""

from test_consultant_value import Conversation, _patch


def started(tmp_path):
    dialogue = Conversation(tmp_path)
    employed = "I'm employed."
    name = "My employer is Northstar Ltd."
    address = "My employer's address is 12 Example Road, Hong Kong."
    phone = "My employer's phone number is +852 2000 1234."
    result = dialogue.turn(f"{employed} {name} {address} {phone}", _patch(updates=[
        ("occupation_status", "employed", employed), ("employer_name", "Northstar Ltd", name),
        ("employer_address", "12 Example Road, Hong Kong", address),
        ("employer_phone", "+852 2000 1234", phone),
    ]))
    assert result.case.profile.employer_name == "Northstar Ltd"
    assert result.case.profile.employer_address == "12 Example Road, Hong Kong"
    assert result.case.profile.employer_phone == "+852 2000 1234"
    assert result.case.profile.current_address is None
    return dialogue, result


def test_current_employer_survives_reopen_with_separate_sources(tmp_path):
    dialogue, first = started(tmp_path)
    later = dialogue.turn("Thanks.", _patch())
    for field in ("employer_name", "employer_address", "employer_phone"):
        assert getattr(later.case.profile, field) == getattr(first.case.profile, field)
        evidence = later.case.active_evidence(field)
        assert len(evidence) == 1 and evidence[0].source_event_id == first.event.id


def test_new_employer_does_not_inherit_previous_address_and_phone(tmp_path):
    dialogue, _ = started(tmp_path)
    body = "My employer is Southstar Ltd."
    result = dialogue.turn(body, _patch(updates=[("employer_name", "Southstar Ltd", body)]))
    assert result.case.profile.employer_name == "Southstar Ltd"
    for field in ("employer_address", "employer_phone"):
        assert getattr(result.case.profile, field) is None
        assert not result.case.active_evidence(field)
        assert any(item.fact_key == field and item.superseded for item in result.case.evidence)


def test_same_event_new_contact_is_bound_to_new_employer(tmp_path):
    dialogue, _ = started(tmp_path)
    name = "My employer is Southstar Ltd."
    phone = "My employer's phone number is +852 2000 5678."
    result = dialogue.turn(f"{name} {phone}", _patch(updates=[
        ("employer_name", "Southstar Ltd", name), ("employer_phone", "+852 2000 5678", phone)]))
    assert result.case.profile.employer_phone == "+852 2000 5678"
    assert result.case.profile.employer_address is None
    assert result.case.active_evidence("employer_phone")[0].source_event_id == result.event.id


def test_leaving_employment_retires_current_employer_facts(tmp_path):
    dialogue, _ = started(tmp_path)
    body = "I'm a student."
    result = dialogue.turn(body, _patch(updates=[("occupation_status", "student", body)]))
    for field in ("employer_name", "employer_address", "employer_phone"):
        assert getattr(result.case.profile, field) is None
        assert not result.case.active_evidence(field)
