from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest
from test_consultant_value import APPLICANT, POLICY, TODAY, Model, _patch

from visa_agent.domain.models import Case, CaseProfile, InboundEvent
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.domain.sponsor_location_review import sponsor_location_binding
from visa_agent.llm.guarded import GuardedLLM
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.conversation import _profile_question_text
from visa_agent.workflow.service import WorkflowService


@pytest.mark.parametrize("language,answer,expected", [("en", "No", False), ("en", "Yes.", True),
                                                     ("zh", "不在", False), ("zh", "在的。", True)])
@pytest.mark.parametrize("fault", [None, "unsent", "recipient", "thread", "late", "identity", "payload"])
def test_short_answer_requires_actual_current_dimension_question(tmp_path, language, answer, expected, fault):
    now = datetime.now(UTC)
    case = Case(id="short", external_thread_id="short", applicant_contact=APPLICANT,
        primary_channel="gmail", policy_version=POLICY.version, customer_language=language,
        profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina Example",
            sponsor_relationship="mother", nationality_country="China", application_country="Hong Kong",
            occupation_status="student", visit_purpose="tourism", route_confirmed_standard_visitor=True,
            has_serious_history=False), last_requested_fields=["sponsor_is_in_uk"],
        question_event_ids={"sponsor_is_in_uk": ["question"]})
    case.sponsor_location_statements = parse_sponsor_location_statements("My sponsor does not live in the UK.",
        source_event_id="original", sponsor_name="Mina Example", sponsor_relationship="mother")
    case.sponsor_location_question_binding = sponsor_location_binding(case)
    question = _profile_question_text(case, "sponsor_is_in_uk")
    prior = InboundEvent(id="question", external_thread_id="short", sender=APPLICANT,
        channel="gmail", subject="Fictional", body="What next?", received_at=now - timedelta(minutes=2))
    with closing(SQLiteStore(tmp_path / "test.db")) as store:
        store.commit_event(case, prior, "blocked", question if fault != "payload" else "Unrelated question")
        with store.connection:
            store.connection.execute("UPDATE outbox SET status=?,sent_at=?,recipient=?,external_thread_id=?", (
                "PENDING" if fault == "unsent" else "SENT",
                (now + timedelta(seconds=1) if fault == "late" else now - timedelta(minutes=1)).isoformat(),
                "other@example.test" if fault == "recipient" else APPLICANT,
                "another-thread" if fault == "thread" else "short"))
        if fault == "identity":
            case.sponsor_location_epoch += 1
            store.save_case(case)
        event = InboundEvent(id="answer", external_thread_id="short", sender=APPLICANT,
            channel="gmail", subject="Fictional", body=answer, received_at=now)
        service = WorkflowService(store, POLICY, GuardedLLM(Model(_patch()), max_attempts=1), today_provider=lambda: TODAY)
        saved, _, _ = service.process(event)
        rows = [row for row in saved.sponsor_location_statements if row.source_event_id == event.id]
        if fault is None:
            assert len(rows) == 1
            assert rows[0].dimension == "current_presence" and rows[0].value == expected
            assert rows[0].source_excerpt == answer
            assert rows[0].context_question_event_id == "question"
        else:
            assert rows == []
        assert not saved.profile_confirmed and not saved.final_summary_confirmed
        assert saved.delivery_path is None
