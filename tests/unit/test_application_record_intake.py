"""Untrusted proposal/semantic boundary checks, no model, network or applicant data."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.application_records import (
    ApplicationRecordProposal,
    CollectionDeclarationProposal,
)
from visa_agent.llm.ports import CasePatch
from visa_agent.workflow.record_intake import plan_record_intake


def event(body, identifier="record-intake-1"):
    return InboundEvent(id=identifier, external_thread_id="fictional-record-intake", sender="record@example.test",
                        channel="gmail", subject="Visitor preparation", body=body,
                        received_at=datetime(2026, 9, 6, tzinfo=UTC))


def proposed(body, *, country="Japan", period="May 2023", action="add", reference=None, kind="travel", fields=None):
    values = fields if fields is not None else {"country": country, "period": period}
    return ApplicationRecordProposal.model_validate({
        "action": action, "record": {"kind": kind, "fields": {
            field: {"value": value, "source_excerpt": value} for field, value in values.items()}},
        "source_excerpt": body, "target_reference": {"value": reference, "source_excerpt": reference} if reference else None,
        "confidence": 1,
    })


def assertion(body, *, kind="travel", state="unknown"):
    return CollectionDeclarationProposal(kind=kind, state=state, source_excerpt=body, confidence=1)


def plan(body, records=(), assertions=(), ledger=None, identifier="record-intake-1"):
    return plan_record_intake(event(body, identifier), ledger, case_id="fictional-record-intake-case",
                              records=list(records), declarations=list(assertions))


@pytest.mark.parametrize("body", [
    "我在英国没有亲属，也没有联系人。",
    "我在英國沒有親屬，也沒有聯絡人。",
])
def test_coordinated_uk_contact_absence_preserves_supplied_trip(body):
    trip = "我在2024年7月1日至7月7日去过日本旅游。"
    result = plan(trip + body,
                  [proposed(trip, country="日本", period="2024年7月1日至7月7日")],
                  [assertion(body, kind="uk_contact", state="none_declared")])
    assert result.changed and not result.requires_review
    assert result.ledger.collection_state("uk_contact") == "none_declared"
    assert len(result.ledger.current()) == 1
    assert result.ledger.collection_state("travel") == "partial"


@pytest.mark.parametrize("body", [
    "如果我在英国没有亲属，也没有联系人呢？",
    "我朋友在英国没有亲属，也没有联系人。",
    "我在英国没有亲属，但是有联系人。",
])
def test_coordinated_contact_absence_does_not_drop_scope_or_positive_contact(body):
    result = plan(body, assertions=[assertion(body, kind="uk_contact", state="none_declared")])
    assert not result.changed


@pytest.mark.parametrize("body,country,period", [
    ("I visited Japan in May 2023.", "Japan", "May 2023"),
    ("I have been to Japan in May 2023.", "Japan", "May 2023"),
    ("我2023年夏天去过日本。", "日本", "2023年夏天"),
    ("我在2023年5月去了日本。", "日本", "2023年5月"),
])
def test_explicit_past_trip_retains_original_precision(body, country, period):
    result = plan(body, [proposed(body, country=country, period=period)])
    assert result.changed and not result.requires_review
    record = next(iter(result.ledger.current().values()))
    assert record.fields["country"].value == country and record.fields["period"].value == period
    assert result.ledger.collection_state("travel") == "partial"
    assert record.fields["period"].source_event_id == "record-intake-1"


@pytest.mark.parametrize("body,excerpt", [
    ("My friend visited Japan in May 2023.", "My friend visited Japan in May 2023."),
    ("If I visited Japan in May 2023, would that help?", "I visited Japan in May 2023"),
    ('An example: "I visited Japan in May 2023."', "I visited Japan in May 2023."),
    ("My friend said I visited Japan in May 2023.", "I visited Japan in May 2023."),
    ("I never visited Japan in May 2023.", "visited Japan in May 2023"),
    ("I will travel to Japan in May 2023.", "I will travel to Japan in May 2023."),
    ("I plan to visit Japan in May 2023.", "I plan to visit Japan in May 2023."),
    ("我朋友2023年5月去过日本。", "我朋友2023年5月去过日本。"),
    ("如果我2023年5月去过日本呢？", "我2023年5月去过日本"),
    ("我2023年5月没去过日本。", "我2023年5月没去过日本。"),
    ("Thank you.\nOn Monday someone wrote:\nI visited Japan in May 2023.", "I visited Japan in May 2023."),
])
def test_third_party_hypothetical_reported_negated_future_and_quoted_trip_never_persist(body, excerpt):
    country, period = ("日本", "2023年5月") if "日本" in body else ("Japan", "May 2023")
    result = plan(body, [proposed(excerpt, country=country, period=period)])
    assert not result.changed and result.ledger is None


@pytest.mark.parametrize("body,fields", [
    ("My sister Example Chen lives at 1 Example Road, London, UK.", {"name": "Example Chen", "relationship": "sister", "address": "1 Example Road, London, UK"}),
    ("我姐姐陈示例住在英国伦敦示例路1号。", {"name": "陈示例", "relationship": "姐姐", "address": "英国伦敦示例路1号"}),
])
def test_current_applicant_uk_contact_is_its_own_record(body, fields):
    result = plan(body, [proposed(body, kind="uk_contact", fields=fields)])
    assert result.changed and not result.requires_review
    contact = next(iter(result.ledger.current().values()))
    assert contact.kind == "uk_contact" and contact.fields["name"].value == fields["name"]
    assert not {"funding_source", "current_address", "full_name"}.intersection(contact.fields)


def test_independent_good_trip_is_not_lost_to_an_unrelated_third_party_sentence():
    own = "I visited Japan in May 2023."
    other = "My friend visited Korea in July 2024."
    result = plan(own + " " + other, [proposed(own), proposed(other, country="Korea", period="July 2024")])
    assert result.changed and len(result.ledger.current()) == 1


def test_correction_uses_one_existing_target_preserves_other_fields_and_replay():
    first = "I visited Japan in May 2023."
    ledger = plan(first, [proposed(first)]).ledger
    body = "Please correct the Japan trip to June 2023."
    correction = proposed(body, action="amend", reference="Japan", fields={"period": "June 2023"})
    result = plan(body, [correction], ledger=ledger, identifier="record-intake-2")
    assert result.changed and not result.requires_review
    updated = next(iter(result.ledger.current().values()))
    assert updated.revision == 2 and updated.fields["period"].value == "June 2023"
    assert updated.fields["country"].source_event_id == "record-intake-1"
    # Workflow replay is intercepted before extraction. A direct planner replay
    # also cannot create a new revision from now-unchanged values.
    repeated = plan(body, [correction], ledger=result.ledger, identifier="record-intake-2")
    assert not repeated.changed and repeated.ledger == result.ledger


def test_two_trips_to_same_country_are_not_silently_guessed_by_a_country_reference():
    first = "I visited Japan in May 2023. I visited Japan in June 2024."
    ledger = plan(first, [proposed("I visited Japan in May 2023."), proposed("I visited Japan in June 2024.", period="June 2024")]).ledger
    assert len(ledger.current()) == 2
    body = "Please correct the Japan trip to July 2024."
    result = plan(body, [proposed(body, action="amend", reference="Japan", fields={"period": "July 2024"})],
                  ledger=ledger, identifier="record-intake-2")
    assert result.requires_review and not result.changed and result.ledger == ledger
    assert "unique" in result.reason


@pytest.mark.parametrize("body", ["My friend will correct the Japan trip to June 2023.",
                                   "Do not correct the Japan trip to June 2023.",
                                   "If I correct the Japan trip to June 2023, will it help?",
                                   "Please correct my friend's Japan trip to June 2023.",
                                   "I visited Japan in June 2023."])
def test_noncurrent_or_declined_correction_cannot_change_the_target(body):
    ledger = plan("I visited Japan in May 2023.", [proposed("I visited Japan in May 2023.")]).ledger
    result = plan(body, [proposed(body, action="amend", reference="Japan", fields={"period": "June 2023"})],
                  ledger=ledger, identifier="record-intake-2")
    assert not result.changed and result.ledger == ledger


def test_fields_from_two_separate_source_statements_are_not_merged():
    body = "I visited Japan in May 2023. I visited Korea in June 2024."
    result = plan(body, [proposed("I visited Japan in May 2023.", period="June 2024")])
    assert result.requires_review and result.ledger is None


@pytest.mark.parametrize("body,kind,state", [
    ("I cannot remember my travel history.", "travel", "unknown"),
    ("I have never travelled abroad.", "travel", "none_declared"),
    ("我记不清我的出境记录。", "travel", "unknown"),
    ("我从未出国。", "travel", "none_declared"),
    ("I have no UK contacts.", "uk_contact", "none_declared"),
    ("我在英国没有联系人。", "uk_contact", "none_declared"),
])
def test_explicit_collection_state_is_saved_separately_from_trip_dates(body, kind, state):
    result = plan(body, assertions=[assertion(body, kind=kind, state=state)])
    assert result.changed and not result.requires_review and not result.ledger.revisions
    assert result.ledger.collection_state(kind) == state


@pytest.mark.parametrize("body,state", [
    ("I have no concerns about my travel history.", "none_declared"),
    ("I want my complete travel history.", "complete_declared"),
    ("My travel history is not complete.", "complete_declared"),
    ("I am unsure whether my travel history is complete.", "complete_declared"),
    ("My friend's travel history is complete.", "complete_declared"),
    ("If I have never travelled abroad, does that matter?", "none_declared"),
    ("My travel dates are unsure.", "unknown"),
    ("I have no UK relatives.", "none_declared"),
    ("Thanks, everything looks good.", "complete_declared"),
    ("My friend cannot remember his travel history.", "unknown"),
    ("我朋友记不清出境记录。", "unknown"),
    ("I might say I have never travelled abroad.", "none_declared"),
    ("我想说我从未出国。", "none_declared"),
])
def test_keyword_presence_is_not_an_absence_or_exhaustiveness_assertion(body, state):
    result = plan(body, assertions=[assertion(body, state=state)])
    assert not result.changed and result.ledger is None


def test_explicit_full_history_after_a_trip_is_saved_without_inventing_missing_trip_purpose():
    first = "I visited Japan in May 2023."
    ledger = plan(first, [proposed(first)]).ledger
    body = "This is my full travel history."
    result = plan(body, assertions=[assertion(body, state="complete_declared")], ledger=ledger, identifier="record-intake-2")
    assert result.changed and result.ledger.collection_state("travel") == "complete_declared"
    assert "purpose" not in next(iter(result.ledger.current().values())).fields


def test_record_proposal_schema_has_no_workflow_or_identifier_authority():
    data = proposed("I visited Japan in May 2023.").model_dump()
    for key in ("record_id", "expected_revision_digest", "consent", "profile_confirmed", "delivery_allowed"):
        with pytest.raises(ValidationError):
            ApplicationRecordProposal.model_validate({**data, key: True})
    with pytest.raises(ValidationError):
        CasePatch(updates=[], ambiguities=[], application_records=[proposed("I visited Japan in May 2023.")] * 21)


def test_negated_correction_value_is_rejected_but_explicit_replacement_is_kept():
    first = "I visited Japan in May 2023."
    ledger = plan(first, [proposed(first)]).ledger
    body = "Please correct the Japan trip: not May 2023 but June 2023."
    wrong = plan(body, [proposed(body, action="amend", reference="Japan", fields={"period": "May 2023"})],
                 ledger=ledger, identifier="record-intake-2")
    assert wrong.requires_review and wrong.ledger == ledger
    right = plan(body, [proposed(body, action="amend", reference="Japan", fields={"period": "June 2023"})],
                 ledger=ledger, identifier="record-intake-2")
    assert right.changed and not right.requires_review
    assert next(iter(right.ledger.current().values())).fields["period"].value == "June 2023"


@pytest.mark.parametrize("fields", [
    {"country": "May 2023", "period": "Japan"},
    {"country": "tourism", "period": "May 2023"},
    {"country": "Japan", "period": "Japan"},
    {"country": "Japan", "period": "May 2023", "purpose": "Japan"},
])
def test_literal_substrings_cannot_be_assigned_to_wrong_travel_fields(fields):
    body = "I visited Japan in May 2023 for tourism."
    result = plan(body, [proposed(body, fields=fields)])
    assert result.requires_review and result.ledger is None


def test_one_model_entry_cannot_join_country_from_one_sentence_and_date_from_another():
    body = "I visited Japan in May 2023. I visited Korea in June 2024."
    result = plan(body, [proposed(body, country="Japan", period="June 2024")])
    assert result.requires_review and result.ledger is None


@pytest.mark.parametrize("body", ["其余的出境记录我暂时记不清，需要再核实。",
                                   "I cannot remember the remaining trips in my travel history."])
def test_explicit_uncertainty_in_a_model_partial_proposal_defers_instead_of_escalating(body):
    proposal = assertion(body, state="partial")
    result = plan(body, assertions=[proposal])
    assert result.changed and not result.requires_review
    assert result.ledger.collection_state("travel") == "unknown"
    assert proposal.state == "partial"  # preserve original diagnostic output


def test_negated_uncertainty_is_not_stored_as_a_deferral():
    body = "I am not unsure about my travel history."
    result = plan(body, assertions=[assertion(body)])
    assert not result.changed and result.ledger is None


def test_model_target_hint_cannot_override_the_literal_customer_reference():
    one, two = "I visited Japan in May 2023.", "I visited Korea in June 2024."
    ledger = plan(one + " " + two, [proposed(one), proposed(two, country="Korea", period="June 2024")]).ledger
    body = "Please correct the Korea trip to July 2024."
    command = proposed(body, action="amend", reference="Korea", fields={"period": "July 2024"})
    command.target_reference.value = "Japan May 2023"  # untrusted hint, not an identifier
    result = plan(body, [command], ledger=ledger, identifier="record-intake-2")
    assert result.changed and not result.requires_review
    trips = {r.fields["country"].value: r for r in result.ledger.current().values()}
    assert trips["Japan"].fields["period"].value == "May 2023" and trips["Japan"].revision == 1
    assert trips["Korea"].fields["period"].value == "July 2024" and trips["Korea"].revision == 2


def test_unverified_new_field_is_not_accepted_when_mixed_with_a_grounded_date_correction():
    first = "I visited Japan in May 2023."
    ledger = plan(first, [proposed(first)]).ledger
    body = "Please correct the Japan trip to June 2023."
    command = proposed(body, action="amend", reference="Japan", fields={"period": "June 2023", "purpose": "business"})
    command.record.fields.purpose.source_excerpt = "Japan trip"
    result = plan(body, [command], ledger=ledger, identifier="record-intake-2")
    assert result.requires_review and not result.changed and result.ledger == ledger


def test_echoing_only_an_unsupported_old_value_cannot_silently_drop_a_requested_correction():
    first = "I visited Japan in May 2023."
    ledger = plan(first, [proposed(first)]).ledger
    body = "Please correct the Japan trip to June 2023."
    command = proposed(body, action="amend", reference="Japan", fields={"period": "May 2023"})
    command.record.fields.period.source_excerpt = "Japan trip"
    result = plan(body, [command], ledger=ledger, identifier="record-intake-2")
    assert result.requires_review and not result.changed


def test_one_valid_change_does_not_hide_an_explicitly_requested_but_ungrounded_other_change():
    first = "I visited Japan in May 2023 for tourism."
    ledger = plan(first, [proposed(first, fields={"country": "Japan", "period": "May 2023", "purpose": "tourism"})]).ledger
    body = "Please correct the Japan trip period to June 2023 and purpose to business."
    command = proposed(body, action="amend", reference="Japan", fields={"period": "June 2023", "purpose": "tourism"})
    command.record.fields.purpose.source_excerpt = "Japan trip"
    result = plan(body, [command], ledger=ledger, identifier="record-intake-2")
    assert result.requires_review and not result.changed and result.ledger == ledger
