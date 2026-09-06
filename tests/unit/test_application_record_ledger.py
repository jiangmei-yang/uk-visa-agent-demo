"""Record integrity only: semantic ownership and workflow integration are separate."""

import json

import pytest
from pydantic import ValidationError

from visa_agent.domain.application_records import (
    ApplicationRecordLedger,
    QuotedText,
    RecordCommand,
    TravelFields,
    TravelInput,
    UKContactFields,
    UKContactInput,
    apply_record_commands,
)


def quote(value, excerpt=None):
    return QuotedText(value=value, source_excerpt=excerpt or value)


def travel(country="日本", period="2023年5月"):
    return RecordCommand(action="add", record=TravelInput(fields=TravelFields(
        country=quote(country), period=quote(period), purpose=quote("旅游"))))


def seeded():
    ledger = ApplicationRecordLedger(case_id="fictional-case-a")
    return apply_record_commands(ledger, case_id=ledger.case_id, event_id="event-1",
                                 body="我2023年5月去日本旅游，2024年去韩国旅游。",
                                 commands=[travel(), travel("韩国", "2024年")])


def amend(ledger, *, period="2023年6月", country="日本"):
    target = next(record for record in ledger.current().values() if record.fields["country"].value == country)
    return RecordCommand(action="amend", record=TravelInput(fields=TravelFields(period=quote(period))),
                         target_id=target.record_id, expected_revision_digest=target.digest(),
                         change_excerpt=f"更正{country}那次，是{period}")


def apply(ledger, commands, body, event="event-2"):
    return apply_record_commands(ledger, case_id=ledger.case_id, event_id=event, body=body, commands=commands)


def test_each_trip_preserves_its_own_fields_excerpts_and_source_event_after_json_reload():
    ledger = ApplicationRecordLedger.model_validate_json(seeded().model_dump_json())
    records = list(ledger.current().values())
    assert len(records) == 2 and records[0].record_id != records[1].record_id
    assert [r.fields["country"].value for r in records] == ["日本", "韩国"]
    assert [r.fields["period"].value for r in records] == ["2023年5月", "2024年"]
    assert {f.source_event_id for r in records for f in r.fields.values()} == {"event-1"}
    assert {f.provenance_state for r in records for f in r.fields.values()} == {"extracted_unverified"}
    assert all(len(f.source_body_sha256) == 64 for r in records for f in r.fields.values())


@pytest.mark.parametrize("period", ["2023年", "2023年夏天", "May 2023", "about two years ago"])
def test_partial_dates_keep_customer_precision_without_inventing_days(period):
    ledger = ApplicationRecordLedger(case_id="fictional-case-a")
    result = apply(ledger, [travel(period=period)], f"我去日本旅游，时间是{period}")
    assert next(iter(result.current().values())).fields["period"].value == period


def test_missing_period_is_unknown_not_a_guessed_date_or_complete_history():
    ledger = ApplicationRecordLedger(case_id="fictional-case-a")
    result = apply(ledger, [RecordCommand(action="add", record=TravelInput(fields=TravelFields(
        country=quote("Japan"))))], "I have visited Japan; I cannot remember when.")
    assert set(next(iter(result.current().values())).fields) == {"country"}
    assert "confirmed_complete" not in result.model_dump_json()


def test_targeted_correction_preserves_other_trip_and_unchanged_field_sources():
    ledger = seeded()
    before = ledger.model_dump_json()
    command = amend(ledger)
    result = apply(ledger, [command], "更正日本那次，是2023年6月。韩国那次没变。")
    assert ledger.model_dump_json() == before
    target = result.current()[command.target_id]
    assert target.revision == 2 and target.fields["period"].value == "2023年6月"
    assert target.fields["period"].source_event_id == "event-2"
    assert target.fields["country"].source_event_id == "event-1"
    original = ledger.current()[command.target_id]
    assert target.predecessor_digest == original.digest()
    assert result.revisions[0] == ledger.revisions[0]
    other_id = next(key for key in ledger.current() if key != command.target_id)
    assert result.current()[other_id] == ledger.current()[other_id]
    assert result.fingerprint() != ledger.fingerprint()
    assert ApplicationRecordLedger.model_validate_json(result.model_dump_json()) == result


def test_replay_is_idempotent_but_same_event_with_changed_body_or_commands_is_rejected():
    ledger = seeded()
    command = amend(ledger)
    result = apply(ledger, [command], "更正日本那次，是2023年6月")
    replay = apply(result, [command], "更正日本那次，是2023年6月")
    assert replay == result and replay is not result
    for body, commands in [("更正日本那次，是2023年6月。", [command]), ("更正日本那次，是2023年6月", [])]:
        with pytest.raises(ValueError, match="reinterpreted"):
            apply(result, commands, body)


def test_stale_revision_cannot_overwrite_a_newer_correction():
    ledger = seeded()
    old_command = amend(ledger)
    changed = apply(ledger, [old_command], "更正日本那次，是2023年6月")
    with pytest.raises(ValueError, match="stale"):
        apply(changed, [old_command], "更正日本那次，是2023年6月", event="event-3")


def test_unknown_or_foreign_case_target_is_never_guessed_from_country():
    ledger = seeded()
    command = amend(ledger)
    other = ApplicationRecordLedger(case_id="fictional-case-b")
    with pytest.raises(ValueError, match="foreign"):
        apply(other, [command], "更正日本那次，是2023年6月")
    with pytest.raises(ValueError, match="bind its case"):
        apply_record_commands(ledger, case_id=other.case_id, event_id="event-2",
                              body="更正日本那次，是2023年6月", commands=[command])


def test_failed_second_command_rolls_back_the_entire_batch():
    ledger = seeded()
    original = ledger.model_dump_json()
    command = amend(ledger)
    wrong = amend(ledger, country="韩国", period="2025年")
    with pytest.raises(ValueError, match="absent"):
        apply(ledger, [command, wrong], "更正日本那次，是2023年6月")
    assert ledger.model_dump_json() == original


def test_withdrawal_keeps_a_tombstone_and_original_source_without_touching_other_records():
    ledger = seeded()
    target = next(iter(ledger.current().values()))
    command = RecordCommand(action="withdraw", record=TravelInput(fields=TravelFields()),
                            target_id=target.record_id, expected_revision_digest=target.digest(),
                            change_excerpt="删掉日本那次，是我记错了")
    result = apply(ledger, [command], command.change_excerpt)
    assert len(result.current()) == 1 and len(result.current(include_withdrawn=True)) == 2
    assert result.current(include_withdrawn=True)[target.record_id].fields == target.fields
    assert not result.current(include_withdrawn=True)[target.record_id].active
    assert result.fingerprint() != ledger.fingerprint()
    with pytest.raises(ValueError, match="withdrawn"):
        apply(result, [amend(ledger)], "更正日本那次，是2023年6月", event="event-3")


def test_contact_keeps_support_separate_from_sponsor_or_applicant_profile():
    ledger = ApplicationRecordLedger(case_id="fictional-case-a")
    body = "My sister Fictional Example lives at 1 Fictional Street, London. She will host me but will not pay."
    command = RecordCommand(action="add", record=UKContactInput(fields=UKContactFields(
        name=quote("Fictional Example"), relationship=quote("sister"),
        address=quote("1 Fictional Street, London"), support_details=quote("will host me but will not pay"))))
    result = apply(ledger, [command], body)
    contact = next(iter(result.current().values()))
    assert contact.kind == "uk_contact" and contact.fields["relationship"].value == "sister"
    assert not {"funding_source", "sponsor_name", "current_address", "has_serious_history",
                "profile_confirmed", "consent"}.intersection(contact.fields)


@pytest.mark.parametrize("value,excerpt", [("2023-05-01", "May 2023"), ("Japan", "日本"), (" ", " ")])
def test_record_values_cannot_invent_precision_translate_or_be_blank(value, excerpt):
    with pytest.raises(ValidationError):
        quote(value, excerpt)


def test_matching_value_elsewhere_cannot_rescue_a_fabricated_source_excerpt():
    ledger = ApplicationRecordLedger(case_id="fictional-case-a")
    command = RecordCommand(action="add", record=TravelInput(fields=TravelFields(
        country=quote("Japan", "I visited Japan"))))
    with pytest.raises(ValueError, match="absent"):
        apply(ledger, [command], "Japan was mentioned, but not in the proposed sentence.")


@pytest.mark.parametrize("field", ["consent", "funding_source", "route_confirmed_standard_visitor", "full_name"])
def test_extra_profile_or_authority_fields_are_not_record_fields(field):
    with pytest.raises(ValidationError):
        UKContactFields.model_validate({"name": quote("Example").model_dump(), field: quote("yes").model_dump()})


@pytest.mark.parametrize("corruption", ["revision", "predecessor", "type", "fields", "resurrection"])
def test_reloading_rejects_broken_revision_chains(corruption):
    ledger = seeded()
    target = next(iter(ledger.current().values()))
    command = amend(ledger)
    data = json.loads(apply(ledger, [command], "更正日本那次，是2023年6月").model_dump_json())
    if corruption == "revision":
        data["revisions"][-1]["revision"] = 7
    elif corruption == "predecessor":
        data["revisions"][-1]["predecessor_digest"] = "wrong"
    elif corruption == "type":
        data["revisions"][-1]["kind"] = "uk_contact"
    elif corruption == "fields":
        data["revisions"][-1]["fields"]["profile_confirmed"] = data["revisions"][-1]["fields"]["country"]
    else:
        data["revisions"][0]["active"] = False
    with pytest.raises(ValidationError):
        ApplicationRecordLedger.model_validate(data)
    assert target.active
