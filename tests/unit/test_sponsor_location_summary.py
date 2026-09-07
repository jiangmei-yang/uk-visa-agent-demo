import pytest

from visa_agent.domain.models import Case, CaseProfile
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.workflow.conversation import confirmation_message, summary_fingerprint
from visa_agent.workflow.sponsor_location_summary import sponsor_location_summary_rows


def example(language):
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version="test", customer_language=language, profile=CaseProfile(funding_source="personal_sponsor",
            sponsor_name="Mina Example", sponsor_relationship="mother", sponsor_is_in_uk=False))
    case.sponsor_location_statements = parse_sponsor_location_statements(
        "My sponsor does not live in the UK but is in the UK now.", source_event_id="private-source-id",
        sponsor_name="Mina Example", sponsor_relationship="mother")
    return case


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("profile_only", [False, True])
def test_both_confirmation_emails_show_separate_customer_facts(language, profile_only):
    case = example(language)
    before = case.model_dump_json()
    reply = confirmation_message(case, profile_only=profile_only)
    if language == "en":
        assert "Sponsor lives in the UK (as reported by you): No" in reply
        assert "Sponsor currently physically in the UK (as reported by you): Yes" in reply
        assert "do not establish" in reply
        assert "Sponsor Is In Uk" not in reply
    else:
        assert "资助人是否居住在英国（按你提供的信息）：否" in reply
        assert "资助人目前是否人在英国（按你提供的信息）：是" in reply
        assert "不代表已核实" in reply
        assert "资助人的英国情况回答" not in reply
    assert "private-source-id" not in reply
    assert case.model_dump_json() == before


@pytest.mark.parametrize("language", ["en", "zh"])
@pytest.mark.parametrize("state", ["missing", "conflict", "new_identity", "new_epoch", "self_funded"])
def test_incomplete_or_old_sponsor_data_is_not_presented_as_current_fact(language, state):
    case = example(language)
    if state == "missing":
        case.sponsor_location_statements.pop()
    elif state == "conflict":
        case.sponsor_location_statements.append(case.sponsor_location_statements[0].model_copy(update={"value": True}))
    elif state == "new_identity":
        case.profile.sponsor_name = "Another Person"
    elif state == "new_epoch":
        case.sponsor_location_epoch += 1
    else:
        case.profile.funding_source = "self"
    rows = sponsor_location_summary_rows(case)
    if state == "self_funded":
        assert rows == []
    elif state == "conflict":
        assert ("Conflicting statements" if language == "en" else "有不同说法") in rows[0]
    else:
        unknown = "Not yet confirmed" if language == "en" else "尚未确认"
        assert unknown in rows[1]
        if state in {"new_identity", "new_epoch"}:
            assert unknown in rows[0]


def test_changing_a_displayed_dimension_changes_both_confirmation_fingerprints():
    case = example("en")
    before = [summary_fingerprint(case, include_documents=value) for value in (False, True)]
    case.sponsor_location_statements[1] = case.sponsor_location_statements[1].model_copy(update={"value": False})
    after = [summary_fingerprint(case, include_documents=value) for value in (False, True)]
    assert all(a != b for a, b in zip(before, after, strict=True))


@pytest.mark.parametrize("language", ["en", "zh"])
def test_pack_profile_rows_use_same_facts_in_english_without_legacy_boolean(language):
    from visa_agent.delivery.pack import _profile_rows

    case = example(language)
    before = case.model_dump_json()
    rows = _profile_rows(case)
    assert "Sponsor lives in the UK (as reported by you): No" in rows
    assert "Sponsor currently physically in the UK (as reported by you): Yes" in rows
    assert not any(row.startswith("Sponsor is in the UK:") for row in rows)
    assert not any("private-source-id" in row for row in rows)
    assert case.model_dump_json() == before
