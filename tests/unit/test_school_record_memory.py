"""Independent school-memory contracts with synthetic text and captured transport.

SENT positives use the real isolated outbox dispatcher, not assigned SENT flags.
This is offline unit evidence, not delivery to a school or a real applicant.
"""

from contextlib import closing
from datetime import UTC, datetime

import pytest

from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, InboundEvent
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.document_preparation import (
    SCHOOL_RECORD_TOPIC,
    reviewed_school_record,
    school_record_followup,
    school_record_guidance,
    school_record_reported,
    school_record_resolved,
    school_record_unavailable,
    sent_school_record_context,
)

ZH = "学校不提供在读证明，我只能下载网上在读记录。"
EN = "My university does not issue enrolment letters. I can only download an online enrolment record."


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("School record unit tests cannot use the network")
    monkeypatch.setattr("socket.create_connection", deny)
    monkeypatch.setattr("socket.socket.connect", deny)


class Capture:
    def __init__(self):
        self.requests = []

    def send(self, request):
        assert request.recipient == "school-memory@example.test" and request.attachment is None
        self.requests.append(request)
        return "synthetic-school-provider-id"


def delivered(tmp_path, language="zh", *, links=True):
    path = tmp_path / "school-memory.db"
    stamp = datetime(2026, 9, 5, 12, tzinfo=UTC)
    event = InboundEvent(id="school-original-event", external_thread_id="school-thread",
        channel="gmail", sender="school-memory@example.test", subject="Fictional school record", body=ZH,
        received_at=stamp, rfc_message_id="<school-original-event@example.test>")
    case = Case(id="school-unit-case", external_thread_id=event.external_thread_id,
        applicant_contact=event.sender, primary_channel="gmail", customer_language=language,
        policy_version="2026-02-25", guidance_events={SCHOOL_RECORD_TOPIC: event.id})
    body = "Hello.\n\n" + school_record_guidance(language)
    if links:
        body += "\nGOV.UK: https://www.gov.uk/standard-visitor"
    capture = Capture()
    with closing(SQLiteStore(path)) as store:
        store.commit_event(case, event, "blocked", body)
        assert not sent_school_record_context(case, store.list_outbox())
        outcomes = OutboxDispatcher(store, capture, channel="gmail").dispatch_due(stamp)
        assert len(outcomes) == 1 and outcomes[0].status == "SENT"
        assert capture.requests[0].body == body
    with closing(SQLiteStore(path)) as store:
        reloaded = store.get_case(case.id)
        rows = store.list_outbox()
        assert rows[0]["provider_message_id"] and rows[0]["sent_at"]
        assert rows[0]["payload"] == capture.requests[0].body
        return reloaded, rows


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("links", [False, True])
def test_actual_complete_sent_context_survives_reopen_links_and_language_switch(tmp_path, language, links):
    case, rows = delivered(tmp_path, language, links=links)
    assert sent_school_record_context(case, rows)
    case.customer_language = "en" if language == "zh" else "zh"
    assert sent_school_record_context(case, rows)


@pytest.mark.parametrize("status", ["PENDING", "FAILED", "RETRY", "SENDING", "AMBIGUOUS"])
def test_only_sent_status_can_supply_context(tmp_path, status):
    case, rows = delivered(tmp_path)
    assert not sent_school_record_context(case, [dict(rows[0], status=status)])


@pytest.mark.parametrize("change", ["other_case", "other_event", "no_event_marker", "truncated_start",
                                   "truncated_end", "first_paragraph_only", "missing_provider", "missing_sent_at"])
def test_incomplete_or_unbound_delivery_cannot_supply_school_context(tmp_path, change):
    case, rows = delivered(tmp_path)
    row = dict(rows[0])
    controlled = school_record_guidance("zh")
    if change == "other_case":
        row["case_id"] = "neighbour-case"
    elif change == "other_event":
        row["event_id"] = "neighbour-event"
    elif change == "no_event_marker":
        case.guidance_events = {}
    elif change == "truncated_start":
        row["payload"] = controlled[1:]
    elif change == "truncated_end":
        row["payload"] = controlled[:-1]
    elif change == "first_paragraph_only":
        row["payload"] = controlled.split("\n\n")[0]
    elif change == "missing_provider":
        row["provider_message_id"] = None
    elif change == "missing_sent_at":
        row["sent_at"] = None
    assert not sent_school_record_context(case, [row])


@pytest.mark.parametrize("body", [ZH, EN])
def test_current_own_online_only_school_record_is_reported(body):
    assert school_record_reported(body)
    assert not school_record_resolved(body)


@pytest.mark.parametrize("body", [
    "学校还没回复，尚不知道是否提供在读证明。我只能下载网上在读记录。",
    "学校没有说不提供在读证明，目前只确认可以网上下载在读记录。",
    "学校不是不提供在读证明，只是还没有回复。我只能下载网上在读记录。",
    "My university has not replied yet. I can only download an online enrolment record.",
    "My university has not said it does not issue enrolment letters. I can only download an online enrolment record.",
    "如果学校不提供在读证明，我只能下载网上在读记录该怎么办？",
    "If my university does not issue enrolment letters, I can only download an online enrolment record.",
    "‘学校不提供在读证明，我只能下载网上在读记录。’",
    '"My university does not issue enrolment letters. I can only download an online enrolment record."',
    "我同学的学校不提供在读证明，他只能下载网上在读记录。",
    "My friend's university does not issue enrolment letters. I can only download an online enrolment record.",
])
def test_unknown_negated_quoted_or_other_person_difficulty_is_not_recorded(body):
    assert not school_record_reported(body)


@pytest.mark.parametrize("body", [
    "学校现在可以开在读证明了。", "我已经拿到在读证明了。", "我收到了学校证明。",
    "I have received my enrolment letter.", "My university has now issued my enrolment letter.",
    "My school finally provided my enrolment letter.",
])
def test_current_affirmative_resolution_is_recognised(body):
    assert school_record_resolved(body)


@pytest.mark.parametrize("body", [
    "我还没拿到在读证明。", "我没有拿到学校证明。", "学校现在还不能开在读证明。",
    "学校现在还没有回复。", "我只下载了网上在读记录，还没拿到在读证明。",
    "My university has not issued my enrolment letter.",
    "My school now cannot issue an enrolment letter.",
    "I have not received my enrolment letter.", "I have never received my enrolment letter.",
    "My university has not replied yet.", "I downloaded my online enrolment record.",
    "如果我拿到在读证明，再告诉你。", "If I have received my enrolment letter, I will tell you.",
    "‘我已经拿到在读证明了。’", '"I have received my enrolment letter."',
    "我同学拿到在读证明了。", "姐姐拿到在读证明了。", "她终于拿到在读证明了。",
    "My friend has received his enrolment letter.", "My sister has received an enrolment letter.",
    "学校现在可以开在读证明了吗？", "Have I received my enrolment letter?",
    "> 我已经拿到在读证明了。\n我自己的学校还没回复。",
])
def test_unresolved_or_noncurrent_statement_does_not_clear_school_memory(body):
    assert not school_record_resolved(body)


@pytest.mark.parametrize("body", [
    "My university does not issue enrolment letters. I can only download my bank statement online.",
    "My school does not issue enrolment letters. I can only obtain an electronic passport copy.",
    "学校不提供在读证明。我只能在网上下载银行流水。",
    "学校不提供在读证明。我只能下载网上银行流水，没有网上在读记录。",
])
def test_unrelated_online_document_cannot_become_an_existing_enrolment_record(body):
    assert not school_record_reported(body)


@pytest.mark.parametrize("body", [
    "Your university does not issue enrolment letters. I can only download my enrolment record from an online portal.",
    "Another university does not issue enrolment letters. I can only download my enrolment record from an online portal.",
    "另一所大学不提供在读证明。我只能下载我自己的网上在读记录。",
    "你的学校不提供在读证明。我只能下载我自己的网上在读记录。",
])
def test_another_school_policy_and_own_record_cannot_be_joined_into_own_obstacle(body):
    assert not school_record_reported(body)


@pytest.mark.parametrize("body", [
    "我不是没有拿到在读证明。",
    "I have not failed to receive my enrolment letter.",
])
def test_double_negation_is_not_an_unambiguous_resolution(body):
    assert not school_record_resolved(body)


@pytest.mark.parametrize("body", [
    "My brother's university does not issue visa-specific enrolment letters. "
    "I can only download my enrolment record from my own university's online portal.",
    "哥哥的学校不提供签证专用的在读证明，我只能在网上下载自己的在读记录。",
])
def test_relative_school_policy_does_not_describe_applicants_school(body):
    assert not school_record_reported(body)


@pytest.mark.parametrize("body", [
    "I no longer have the online record. What should I do next?",
    "网上记录我已经打不开了。那下一步怎么办？",
])
def test_changed_record_availability_is_not_generic_followup(body):
    assert not school_record_followup(body)


@pytest.mark.parametrize("body", [
    "My university has now issued an enrolment letter. What should I do next?",
    "学校现在能开在读证明了。那下一步怎么办？",
])
def test_independent_question_does_not_hide_affirmative_resolution(body):
    assert school_record_resolved(body)


@pytest.mark.parametrize("language,body", [
    ("zh", "学校不提供签证专用的在读证明，我只能在网上下载在读记录，学校也提供普通在读证明。"),
    ("en", "My university does not issue visa-specific enrolment letters. I can only download my enrolment "
     "record from its online portal. It also provides ordinary enrolment letters."),
])
def test_preparation_does_not_claim_school_offers_only_online_records(language, body):
    answer = reviewed_school_record(body, language)
    assert answer and ("网上在读记录" in answer or "online enrolment record" in answer)
    assert "学校只提供" not in answer and "only provides" not in answer


@pytest.mark.parametrize("body", [
    "网上记录我已经打不开了。那下一步怎么办？",
    "I no longer have the online record. What should I do next?",
])
def test_current_record_access_loss_retires_old_access_advice(body):
    assert school_record_unavailable(body)
    assert not school_record_resolved(body)


@pytest.mark.parametrize("body", [
    "如果网上记录我已经打不开了。那下一步怎么办？",
    "If I no longer have the online record. What should I do next?",
    "‘网上记录我已经打不开了。’", '"I no longer have the online record."',
    "我同学的网上记录已经打不开了。", "My friend no longer has the online record.",
    "我不是打不开网上记录。", "I have not lost the online record.",
])
def test_record_access_loss_must_be_current_own_assertion(body):
    assert not school_record_unavailable(body)
