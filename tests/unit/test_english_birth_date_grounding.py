"""Full English dates must normalize without accepting another person's birthday."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from visa_agent.domain.date_evidence import canonical_date_value, date_is_grounded
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message, validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.service import WorkflowService


def event(body, requested_fields=None):
    return InboundEvent(id="english-birthday", external_thread_id="fictional-thread",
                        sender="fictional@example.test", subject="UK visit", body=body,
                        received_at=datetime(2026, 9, 4, tzinfo=UTC), requested_fields=requested_fields or [])


def birthday(value="8 July 1992", excerpt="My date of birth is 8 July 1992"):
    return FactUpdate(field="date_of_birth", value=value, source_excerpt=excerpt, confidence=1)


@pytest.mark.parametrize("value,expected", [
    ("8 July 1992", "1992-07-08"), ("July 8, 1992", "1992-07-08"),
    ("July 8 1992", "1992-07-08"), ("8 JULY 1992", "1992-07-08"),
    (" 8  July\n1992 ", "1992-07-08"), ("８ July １９９２", "1992-07-08"),
    ("8 Jul 1992", "1992-07-08"), ("29 February 2000", "2000-02-29"),
])
def test_explicit_named_month_value_normalizes_and_stays_grounded(value, expected):
    assert canonical_date_value(value) == expected
    assert date_is_grounded(expected, f"My date of birth is {value}.", allow_shared_year=False)


@pytest.mark.parametrize("value", [
    "8 July", "July 1992", "8 July 92", "08/07/1992", "29 February 1999", "31 April 1992",
    "0 July 1992", "32 July 1992", "8 Julu 1992", "8 July 19920", "8 July 1992.3",
    "My date of birth is 8 July 1992", "8 July 1992 or 9 July 1992", "July 8, 1992 extra",
])
def test_incomplete_invalid_ambiguous_or_prose_values_are_not_repaired(value):
    assert canonical_date_value(value) == value


@pytest.mark.parametrize("body,excerpt,value,requested", [
    ("My date of birth is 8 July 1992.", "My date of birth is 8 July 1992", "8 July 1992", []),
    ("I was born on July 8, 1992.", "July 8, 1992", "July 8, 1992", []),
    ("My birthday is 8 July 1992. My flight is on 10 November 2026.", "8 July 1992", "1992-07-08", []),
    ("DOB: 8 July 1992", "8 July 1992", "8 July 1992", []),
    ("8 July 1992", "8 July 1992", "8 July 1992", ["date_of_birth"]),
    ("A quick correction: my birth date is 8 July 1992. That is all for now.",
     "my birth date is 8 July 1992", "1992-07-08", []),
    ("My birthday should be 8 July 1992.", "My birthday should be 8 July 1992.", "1992-07-08", []),
    ("Please correct my date of birth to 8 July 1992.",
     "Please correct my date of birth to 8 July 1992.", "1992-07-08", []),
])
def test_current_explicit_applicant_birthday_is_accepted(body, excerpt, value, requested):
    result = validate_case_patch(event(body, requested), CasePatch(updates=[birthday(value, excerpt)], ambiguities=[]))
    assert not result.requires_human_review
    assert len(result.updates) == 1 and result.updates[0].value == "1992-07-08"
    assert result.updates[0].source_excerpt == excerpt


@pytest.mark.parametrize("body,excerpt,value", [
    ("My date of birth is 9 July 1992.", "My date of birth is 9 July 1992", "8 July 1992"),
    ("My date of birth is 8 July.", "My date of birth is 8 July", "8 July 1992"),
    ("My date of birth is 8 July. Travel is in 1992.", "8 July", "8 July 1992"),
    ("My mother was born on 8 July 1992.", "My mother was born on 8 July 1992", "8 July 1992"),
    ("My friend's date of birth is 8 July 1992.", "8 July 1992", "8 July 1992"),
    ("My brother's birthday is 8 July 1992. My date of birth is 9 July 1992.", "8 July 1992", "8 July 1992"),
    ("A sample form says: My date of birth is 8 July 1992.", "My date of birth is 8 July 1992", "8 July 1992"),
    ("The appointment letter is dated 8 July 1992.", "8 July 1992", "8 July 1992"),
    ("I have not provided my birthday.\n> My date of birth is 8 July 1992", "My date of birth is 8 July 1992", "8 July 1992"),
    ("My date of birth is not 8 July 1992; it is 9 July 1992.", "8 July 1992", "8 July 1992"),
    ("My date of birth might be 8 July 1992 or 9 July 1992.", "8 July 1992", "8 July 1992"),
    ("My date of birth is 8 July 1992 or 9 July 1992.", "8 July 1992", "8 July 1992"),
])
@pytest.mark.parametrize("as_iso", [False, True])
def test_wrong_unrelated_quoted_third_party_or_uncertain_birthdays_are_not_accepted(body, excerpt, value, as_iso):
    if as_iso:
        value = "1992-07-08"
    result = validate_case_patch(event(body), CasePatch(updates=[birthday(value, excerpt)], ambiguities=[]))
    assert not result.updates


def test_real_guard_accepts_recorded_failure_shape_first_attempt_and_persists_other_facts(tmp_path):
    body = ("Hello, my name is Mira Vale. My date of birth is 8 July 1992. "
            "I am employed as an accountant, and I would like to visit the UK for a holiday. "
            "My travel dates are not settled yet; I will send them once I have decided.")
    patch = CasePatch(updates=[birthday(),
        FactUpdate(field="full_name", value="Mira Vale", source_excerpt="my name is Mira Vale", confidence=1),
        FactUpdate(field="occupation_status", value="employed", source_excerpt="I am employed as an accountant", confidence=1),
        FactUpdate(field="visit_purpose", value="tourism", source_excerpt="visit the UK for a holiday", confidence=1),
    ], ambiguities=[])

    class Model:
        version = "offline-recorded-shape"
        calls = 0

        def extract_case_patch(self, prepared):
            self.calls += 1
            return patch.model_copy(deep=True)

        render_message = staticmethod(deterministic_fallback_message)

    model = Model()
    database = tmp_path / "case.db"
    store = SQLiteStore(database)
    try:
        guard = GuardedLLM(model)
        workflow = WorkflowService(store, load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml")),
                                   guard, today_provider=lambda: date(2026, 9, 4))
        case, duplicate, plan = workflow.process(event(body))
        assert not duplicate and plan == "blocked" and not guard.last_extraction_fallback
        assert model.calls == 1
        assert case.profile.date_of_birth == date(1992, 7, 8)
        assert case.profile.full_name == "Mira Vale" and case.profile.occupation_status == "employed"
        assert not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None
        assert "date_of_birth" not in case.last_requested_fields
        before = case.model_dump(mode="json")
        case_id = case.id
    finally:
        store.close()
    reopened = SQLiteStore(database)
    try:
        assert reopened.get_case(case_id).model_dump(mode="json") == before
    finally:
        reopened.close()
