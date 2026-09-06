"""A full existing list is not an absence declaration, even if the model clips it."""

import pytest
from test_application_record_intake import assertion, plan, proposed


def existing():
    body = "My sister Example Doe lives in London."
    return plan(body, [proposed(body, kind="uk_contact", fields={
        "name": "Example Doe", "relationship": "sister", "address": "London"})]).ledger


@pytest.mark.parametrize("body,excerpt", [
    ("I have no other UK contacts.", "I have no other UK contacts."),
    ("我在英国只有前面提到的姐姐陈示例，没有其他亲属或联系人了。", "没有其他亲属或联系人了"),
    ("我在英国没有其他联系人。", "我在英国没有其他联系人"),
])
@pytest.mark.parametrize("state", ["none_declared", "complete_declared"])
def test_no_others_preserves_existing_records_and_saves_full_source(body, excerpt, state):
    ledger = existing()
    proposal = assertion(excerpt, kind="uk_contact", state=state)
    result = plan(body, assertions=[proposal], ledger=ledger, identifier="no-others")
    assert result.changed and not result.requires_review
    assert result.ledger.collection_state("uk_contact") == "complete_declared"
    assert result.ledger.current() == ledger.current()
    saved = result.ledger.latest_declarations()["uk_contact"]
    assert saved.source_event_id == "no-others" and saved.source_excerpt in body
    assert "我" in saved.source_excerpt or "I" in saved.source_excerpt
    assert proposal.state == state


@pytest.mark.parametrize("body", [
    "If I have no other UK contacts, is that enough?",
    "I have no other UK contacts?",
    "我在英国没有其他联系人？",
    "我在英国的朋友没有其他联系人。",
    'Please translate "I have no other UK contacts".',
    "I have no other UK contacts but I am unsure.",
])
def test_nonassertions_and_uncertainty_cannot_complete_the_list(body):
    ledger = existing()
    result = plan(body, assertions=[assertion(body, kind="uk_contact", state="none_declared")],
                  ledger=ledger, identifier="not-asserted")
    assert not result.changed and result.ledger == ledger


def test_no_others_without_an_existing_list_cannot_invent_a_complete_or_empty_list():
    body = "I have no other UK contacts."
    result = plan(body, assertions=[assertion(body, kind="uk_contact", state="none_declared")])
    assert not result.changed and result.ledger is None
