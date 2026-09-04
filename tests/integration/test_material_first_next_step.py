"""Material-first requests through real workflow, guard and captured Gmail SENT.

All identities/PDFs are fictional and all model/transport I/O is offline. This
does not establish document authenticity, route eligibility or delivery consent.
"""

from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.documents.natural import DocumentReadResult
from visa_agent.domain.models import CaseStatus, DocumentStatus, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.domain.rules import evaluate_gate
from visa_agent.llm.guarded import GuardedLLM, deterministic_fallback_message
from visa_agent.llm.ports import CasePatch
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import reply_items

TODAY = date(2026, 9, 5)
POLICY = load_policy(Path("knowledge/uk_standard_visitor_2026-02-25.yaml"))
SENDER = "material-first@example.test"
MATERIAL = {"zh": "现在我该先准备哪一份材料？", "en": "Which document should I prepare first?"}


def patch(*, facts=(), questions=(), control=None):
    value = {"updates": [
        {"field": field, "value": item, "source_excerpt": excerpt, "confidence": 1}
        for field, item, excerpt in facts
    ], "ambiguities": [], "customer_questions": [
        {"topic": topic, "source_excerpt": excerpt, "confidence": 1} for topic, excerpt in questions
    ]}
    if control:
        value["preparation_intent"] = {"action": control[0], "source_excerpt": control[1], "confidence": 1}
    return CasePatch.model_validate(value)


class FixedModel:
    def __init__(self, proposal):
        self.proposal = proposal
        self.events = []

    def extract_case_patch(self, event):
        self.events.append(event.model_copy(deep=True))
        return self.proposal.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


class CapturedGmail(GmailAdapter):
    def __init__(self):
        self.requests = []

    def send_reply(self, **request):
        assert request["recipient"] == SENDER and request.get("attachment") is None
        self.requests.append(request)
        return {"id": f"captured-material-{len(self.requests)}"}


class Journey:
    def __init__(self, tmp_path):
        self.path = tmp_path / "material-first.db"
        self.gmail = CapturedGmail()
        self.turns = 0
        self.reads = []

    def turn(self, body, proposal=None, *, attachments=(), unreadable=False):
        from visa_agent.workflow.service import WorkflowService

        self.turns += 1
        event = InboundEvent(
            id=f"material-event-{self.turns}", external_thread_id="material-first-thread",
            sender=SENDER, channel="gmail", subject="Fictional document preparation", body=body,
            received_at=datetime(2026, 9, 5, 12, tzinfo=UTC) + timedelta(minutes=self.turns),
            rfc_message_id=f"<material-event-{self.turns}@example.test>",
            attachment_paths=[str(item) for item in attachments],
        )
        model = FixedModel(proposal if proposal is not None else patch())

        def read(path):
            self.reads.append(path)
            if unreadable:
                raise ValueError("Synthetic unreadable PDF")
            return DocumentReadResult("passport", "en", 1, {
                "passport_expiry_date": ("2034-04-20", 1, "Expiry: 20 April 2034"),
            }, method="offline-captured-reader")

        with closing(SQLiteStore(self.path)) as store:
            guard = GuardedLLM(model)
            case, duplicate, plan = WorkflowService(
                store, POLICY, guard, today_provider=lambda: TODAY, document_reader=read,
            ).process(event)
            assert not duplicate and plan == "blocked" and not guard.last_extraction_fallback
            sender = AutomaticGmailReplySender(self.gmail, store, SENDER)
            sender.withhold_obsolete_unsent()
            outcomes = OutboxDispatcher(store, sender, channel="gmail").dispatch_due(event.received_at)
            assert len(outcomes) == 1 and outcomes[0].status == "SENT"
            row = next(row for row in store.list_outbox() if row["event_id"] == event.id)
            assert row["provider_message_id"] and row["payload"] == self.gmail.requests[-1]["body"]
            assert store.get_case(case.id).model_dump() == case.model_dump()
            return SimpleNamespace(case=case, event=event, body=row["payload"], model=model)

    def known_context(self):
        facts = [
            ("full_name", "Rowan Example", "My full name is Rowan Example."),
            ("nationality_country", "China", "I hold a Chinese passport."),
            ("application_country", "Hong Kong", "I will apply in Hong Kong."),
            ("visit_purpose", "tourism", "I am visiting the UK for tourism."),
            ("occupation_status", "student", "I am a university student."),
            ("funding_source", "self", "I will pay for the trip myself."),
        ]
        return self.turn(" ".join(item[2] for item in facts) + " My travel dates are undecided.", patch(facts=facts))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Material-first tests cannot use network I/O")
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("socket.socket.connect", forbidden)


def assert_no_authority(case):
    assert case.status == CaseStatus.DRAFT and case.delivery_path is None
    assert not case.profile_confirmed and not case.final_summary_confirmed
    assert case.confirmation_kind is None and case.confirmation_fingerprint is None
    assert not evaluate_gate(case.model_copy(deep=True), POLICY, TODAY).allowed


@pytest.mark.parametrize("language", ["zh", "en"])
def test_explicit_material_request_answers_why_and_how_before_missing_dob(tmp_path, language):
    journey = Journey(tmp_path)
    previous = journey.known_context()
    assert previous.case.profile.date_of_birth is None
    question = MATERIAL[language]
    result = journey.turn(question, patch(questions=[("next_step", question)]))
    assert result.case.id == previous.case.id
    assert result.case.next_step_advice.kind == "document"
    assert result.case.next_step_advice.requirement_id == "passport"
    assert result.case.next_step_advice.question_field is None
    assert result.case.question_plan == result.case.last_requested_fields == []
    assert reply_items(result.case)[1] == []
    assert "PDF" in result.body and ("资料页" if language == "zh" else "details page") in result.body
    assert ("核对身份" if language == "zh" else "check your identity") in result.body
    assert ("回复这封邮件" if language == "zh" else "reply with") in result.body
    assert result.case.profile == previous.case.profile
    assert result.case.evidence == previous.case.evidence and result.case.documents == previous.case.documents
    assert result.case.stage == previous.case.stage
    assert result.case.deferred_fields == previous.case.deferred_fields
    assert_no_authority(result.case)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_ordinary_missing_context_allows_only_bounded_identity_document_preparation(tmp_path, language):
    question = MATERIAL[language]
    result = Journey(tmp_path).turn(question, patch(questions=[("next_step", question)]))
    advice = result.case.next_step_advice
    assert advice.kind == "document" and advice.requirement_id == "passport"
    assert "PDF" in result.body
    assert "GOV.UK: https://www.gov.uk/standard-visitor/apply-standard-visitor-visa\n" in result.body
    assert ("路线" if language == "zh" else "route") in result.body
    assert result.case.profile.visit_purpose is None and result.case.profile.occupation_status is None
    assert result.case.profile.funding_source is None and result.case.profile.date_of_birth is None
    assert result.case.profile.route_confirmed_standard_visitor is False
    assert not any(word in result.body for word in ("在读证明", "在职证明", "enrolment letter", "employer letter"))
    assert_no_authority(result.case)


@pytest.mark.parametrize(("language", "prefix", "include_link"), [
    ("zh", "这次不用给我链接。", False),
    ("en", "Don't send links. ", False),
    ("zh", "朋友说‘不用链接’。", True),
    ("en", "My friend said 'no links'. ", True),
    ("zh", "如果以后我说不用链接再说。", True),
    ("en", "If I later say no links, respect that. ", True),
])
def test_material_instructions_obey_only_current_link_preferences(tmp_path, language, prefix, include_link):
    journey = Journey(tmp_path)
    journey.known_context()
    question = MATERIAL[language]
    result = journey.turn(prefix + question, patch(questions=[("next_step", question)]))
    assert result.case.next_step_advice.kind == "document"
    assert result.case.question_plan == [] and "PDF" in result.body
    assert ("https://www.gov.uk/" in result.body) is include_link
    assert_no_authority(result.case)


@pytest.mark.parametrize("question", [
    "下一步做什么？", "我该先补哪项个人信息？",
    "What is the next step?", "Which personal detail should I provide first?",
])
def test_general_or_personal_information_next_step_keeps_existing_fact_plan(tmp_path, question):
    journey = Journey(tmp_path)
    journey.known_context()
    result = journey.turn(question, patch(questions=[("next_step", question)]))
    assert result.case.next_step_advice.kind == "question"
    assert result.case.next_step_advice.question_field == "date_of_birth"
    assert result.case.last_requested_fields == ["date_of_birth"]
    assert_no_authority(result.case)


@pytest.mark.parametrize("body", [
    'My friend asked "Which document should I prepare first?"',
    "If I proceed later, which document should I prepare first?",
    "Do not tell me which document I should prepare first.",
    "朋友问‘现在我该先准备哪一份材料？’",
    "如果我以后继续，现在我该先准备哪一份材料？", "不要告诉我先准备哪一份材料。",
])
def test_noncurrent_or_declined_request_cannot_start_material_first(tmp_path, body):
    journey = Journey(tmp_path)
    journey.known_context()
    result = journey.turn(body, patch(questions=[("next_step", body)]))
    assert result.case.next_step_advice is None or result.case.next_step_advice.kind != "document"
    assert_no_authority(result.case)


@pytest.mark.parametrize("language", ["zh", "en"])
def test_paused_customer_gets_no_new_upload_or_question(tmp_path, language):
    journey = Journey(tmp_path)
    journey.known_context()
    pause = "Please pause my visa preparation."
    journey.turn(pause, patch(control=("pause", pause)))
    question = MATERIAL[language]
    result = journey.turn(question, patch(questions=[("next_step", question)]))
    assert result.case.preparation_paused and result.case.next_step_advice.kind == "paused"
    assert result.case.next_step_advice.question_field is None
    assert result.case.last_requested_fields == [] and "PDF" not in result.body
    assert_no_authority(result.case)


def pdf(tmp_path):
    path = tmp_path / "fictional-passport.pdf"
    document = canvas.Canvas(str(path))
    document.drawString(40, 750, "FICTIONAL TEST FILE. Expiry: 20 April 2034.")
    document.save()
    return path


def test_existing_readable_passport_is_not_requested_again(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    received = journey.turn("Here is my passport PDF.", attachments=[pdf(tmp_path)])
    assert received.case.documents[0].status == DocumentStatus.ACCEPTED_FOR_REVIEW
    question = MATERIAL["en"]
    result = journey.turn(question, patch(questions=[("next_step", question)]))
    assert result.case.next_step_advice.kind == "document"
    assert result.case.next_step_advice.requirement_id == "status_evidence"
    assert "student status" in result.body and "school" in result.body and "PDF" in result.body
    assert result.case.documents == received.case.documents
    assert result.case.profile.date_of_birth is None and len(journey.reads) == 1
    assert_no_authority(result.case)


def test_unreadable_current_attachment_takes_priority_over_next_material(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    question = MATERIAL["en"]
    result = journey.turn(question, patch(questions=[("next_step", question)]),
                          attachments=[pdf(tmp_path)], unreadable=True)
    assert len(journey.reads) == 1 and result.case.open_blockers()
    assert result.case.next_step_advice.kind == "review"
    assert result.case.next_step_advice.requirement_id is None
    assert "fictional-passport.pdf" in result.body and result.case.last_requested_fields == []
    assert_no_authority(result.case)


def test_known_age_conflict_remains_a_review_boundary(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    birth = "My date of birth is 4 March 2020."
    question = MATERIAL["en"]
    result = journey.turn(birth + " " + question, patch(
        facts=[("date_of_birth", "2020-03-04", birth)], questions=[("next_step", question)],
    ))
    assert result.case.profile.date_of_birth == date(2020, 3, 4)
    assert result.case.next_step_advice.kind == "review"
    assert "age" in result.body and result.case.next_step_advice.requirement_id is None
    assert_no_authority(result.case)


def test_no_validated_next_step_does_not_infer_one_from_body(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    result = journey.turn(MATERIAL["en"])
    assert result.case.next_step_advice is None
    assert_no_authority(result.case)


def test_ordinary_attachment_format_question_is_not_material_first(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    question = "Which format should I use for a document attachment?"
    result = journey.turn(question, patch(questions=[("next_step", question)]))
    assert result.case.next_step_advice.kind != "document"
    assert_no_authority(result.case)


def test_independent_faq_is_answered_alongside_one_material_step(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    question = MATERIAL["en"]
    faq = "Do I need to buy flights before applying?"
    result = journey.turn(faq + " " + question, patch(questions=[("booking", faq), ("next_step", question)]))
    assert result.case.next_step_advice.kind == "document"
    assert result.case.next_step_advice.requirement_id == "passport"
    assert "do not need to buy flights" in result.body and "details page" in result.body
    assert result.case.last_requested_fields == []
    assert_no_authority(result.case)


def test_prior_sent_material_request_is_not_reused_for_current_generic_next_step(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    question = MATERIAL["en"]
    first = journey.turn(question, patch(questions=[("next_step", question)]))
    assert first.case.next_step_advice.kind == "document"
    current = "Which personal detail should I provide next?"
    result = journey.turn(current, patch(questions=[("next_step", current)]))
    assert result.case.id == first.case.id
    assert result.case.next_step_advice.kind == "question"
    assert result.case.last_requested_fields == ["date_of_birth"]
    assert_no_authority(result.case)


def test_sourced_rejection_of_visitor_route_cannot_choose_visitor_material(tmp_path):
    journey = Journey(tmp_path)
    journey.known_context()
    statement = "我不按标准访客签证申请。"
    question = MATERIAL["zh"]
    result = journey.turn(statement + question, patch(
        facts=[("route_confirmed_standard_visitor", False, statement)],
        questions=[("next_step", question)],
    ))
    assert any(item.fact_key == "route_confirmed_standard_visitor" and item.value is False
               and not item.superseded for item in result.case.evidence)
    assert result.case.next_step_advice.kind == "review"
    assert result.case.next_step_advice.requirement_id is None
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.delivery_path is None
