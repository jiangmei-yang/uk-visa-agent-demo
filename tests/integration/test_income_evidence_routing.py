import json
from datetime import timedelta
from pathlib import Path

import pytest
from test_consultant_value import POLICY, TODAY, Conversation

from visa_agent.domain.models import Case, CaseProfile
from visa_agent.llm.ports import CasePatch, CustomerQuestion
from visa_agent.workflow.customer_questions import grounded_customer_answer_plan
from visa_agent.workflow.guidance_freshness import REVIEW_AFTER


def test_actual_v10_proposal_gets_useful_answer_without_contradictory_opening(tmp_path, monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Saved proposal replay must not use network")
    monkeypatch.setattr("socket.socket.connect", deny)
    report = json.loads(Path("eval_output/consultant_journey_2026-09-07-v10.json").read_text())
    rows = [row for row in report["results"] if row["journey"] == "self-employed-host-and-funds"][:3]
    conversation = Conversation(tmp_path)
    for row in rows:
        result = conversation.turn(row["input"], CasePatch.model_validate_json(row["raw_model_content"]))
    assert "unsupported" in rows[-1]["raw_model_content"]
    assert "business registration" in result.body and "receipts" in result.body
    assert "don't currently have verified guidance" not in result.body
    assert "actual documents still need checking" in result.body
    assert "gov.uk" in result.body
    assert result.case.profile.occupation_status == "self_employed"
    assert not result.case.profile_confirmed and not result.case.final_summary_confirmed
    assert result.case.delivery_path is None


@pytest.mark.parametrize("occupation", ["self_employed", "employed", None])
@pytest.mark.parametrize("extra", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_only_current_covered_question_loses_generic_boundary(occupation, extra, expired):
    body = "I don't get payslips. What should I use to explain my income?"
    if extra:
        body += " What tax deductions can I claim?"
    case = Case(id="fictional", external_thread_id="fictional", applicant_contact="fictional@example.test",
        policy_version=POLICY.version, profile=CaseProfile(occupation_status=occupation))
    before = case.model_dump_json()
    plan = grounded_customer_answer_plan(body, "en", REVIEW_AFTER + timedelta(days=1) if expired else TODAY,
        case=case, semantic_questions=[CustomerQuestion(topic="unsupported", source_excerpt=body, confidence=1)])
    if occupation == "self_employed" and not extra and not expired:
        assert "unsupported" not in plan.selected_topics
        assert any("business registration" in answer for answer in plan.answers)
    else:
        assert "unsupported" in plan.selected_topics
    assert case.model_dump_json() == before
