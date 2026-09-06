"""Ground natural amendments without turning hints into authority or overwrites."""

import pytest
from test_application_record_intake import plan, proposed

from visa_agent.workflow.record_intake import record_intake_receipt


def contact(*, passport=None):
    body = "My sister Example Doe lives in London."
    values = {"name": "Example Doe", "relationship": "sister", "address": "London"}
    if passport:
        body += f" Her passport number is {passport}."
        values["passport_number"] = passport
    return plan(body, [proposed(body, kind="uk_contact", fields=values)]).ledger


def update(ledger, body, *, reference="Example Doe", value="TEST00002"):
    return plan(body, [proposed(body, kind="uk_contact", action="amend", reference=reference,
                               fields={"passport_number": value})], ledger=ledger, identifier="natural-update")


@pytest.mark.parametrize("body", [
    "补充一下，Example Doe的护照号码是TEST00002。",
    "補充：Example Doe的護照號碼是TEST00002。",
    "To add: Example Doe's passport number is TEST00002.",
    "One more detail, Example Doe's passport number is TEST00002.",
])
def test_supplement_fills_only_a_grounded_missing_field(body):
    ledger = contact()
    result = update(ledger, body)
    assert result.changed and not result.requires_review
    current = next(iter(result.ledger.current().values()))
    assert current.fields["passport_number"].value == "TEST00002"
    assert current.fields["passport_number"].source_event_id == "natural-update"
    assert current.fields["name"] == next(iter(ledger.current().values())).fields["name"]


@pytest.mark.parametrize("body", [
    "刚才Example Doe的护照号码写错了，请更正为TEST00002。",
    "之前Example Doe的护照号码写错了，改正为TEST00002。",
    "剛才Example Doe的護照號碼寫錯了，請更正為TEST00002。",
])
def test_explicit_prior_error_can_replace_a_value_with_new_provenance(body):
    ledger = contact(passport="TEST00001")
    result = update(ledger, body)
    assert result.changed and not result.requires_review
    assert len(result.ledger.revisions) == 2
    assert next(iter(result.ledger.current().values())).fields["passport_number"].value == "TEST00002"
    assert ledger.revisions[0].fields["passport_number"].value == "TEST00001"


def test_supplement_is_not_silent_authority_to_replace_an_existing_value():
    ledger = contact(passport="TEST00001")
    result = update(ledger, "补充一下，Example Doe的护照号码是TEST00002。")
    assert not result.changed and result.requires_review and result.ledger == ledger


@pytest.mark.parametrize("body", [
    "如果刚才Example Doe的护照号码写错了，请更正为TEST00002。",
    "补充一下，Example Doe的护照号码是TEST00002？",
    "刚才Example Doe的护照号码写错了，请更正为TEST00002？",
    "补充一下，不要把Example Doe的护照号码改成TEST00002。",
    "补充一下，Example Doe的护照号码不是TEST00002。",
    "To add: Example Doe's passport number might be TEST00002.",
    'Please translate "补充一下，Example Doe的护照号码是TEST00002。"',
])
def test_question_hypothesis_negation_and_quote_cannot_update(body):
    ledger = contact()
    result = update(ledger, body)
    assert not result.changed and result.ledger == ledger


def test_same_name_is_ambiguous_and_other_case_name_is_not_a_target():
    ledger = contact()
    body = "My brother Example Doe lives in London."
    ledger = plan(body, [proposed(body, kind="uk_contact", fields={
        "name": "Example Doe", "relationship": "brother", "address": "London"})],
        ledger=ledger, identifier="second-contact").ledger
    result = update(ledger, "补充一下，Example Doe的护照号码是TEST00002。")
    assert not result.changed and result.requires_review and result.ledger == ledger
    result = update(ledger, "补充一下，Another Person的护照号码是TEST00002。", reference="Another Person")
    assert not result.changed and result.requires_review and result.ledger == ledger


@pytest.mark.parametrize("language", ["zh", "en"])
def test_receipt_distinguishes_new_details_from_replacing_a_previous_value(language):
    supplement = update(contact(), "补充一下，Example Doe的护照号码是TEST00002。")
    receipt = record_intake_receipt(supplement, "natural-update", language)
    assert ("补充" if language == "zh" else "added the extra") in receipt
    assert ("更正" if language == "zh" else "correction") not in receipt
    correction = update(contact(passport="TEST00001"), "刚才Example Doe的护照号码写错了，请更正为TEST00002。")
    receipt = record_intake_receipt(correction, "natural-update", language)
    assert ("更正" if language == "zh" else "correction") in receipt
