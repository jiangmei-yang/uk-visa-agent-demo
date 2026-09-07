"""Original fictional Gmail wording survives validation, persistence and reply rendering."""

from test_consultant_value import Conversation

from tests.unit.test_ordinary_trip_clause_grounding import BODY
from visa_agent.llm.ports import CasePatch


def test_original_ordinary_trip_facts_survive_next_turn(tmp_path):
    dialogue = Conversation(tmp_path)
    patch = CasePatch.model_validate({"updates": [
        {"field": field, "value": value, "source_excerpt": excerpt, "confidence": 1}
        for field, value, excerpt in [
            ("nationality_country", "China", "我是中国护照"),
            ("application_country", "Hong Kong", "准备从香港申请"),
            ("visit_purpose", "tourism", "想今年11月去伦敦旅游一周"),
            ("occupation_status", "student", "在香港读书"),
            ("funding_source", "self", "费用自己承担"),
        ]], "ambiguities": [], "question_deferrals": [
            {"field": field, "source_excerpt": "具体哪天还没定", "confidence": 1}
            for field in ("planned_arrival_date", "planned_departure_date")],
    })
    for result in (dialogue.turn(BODY, patch),
                   dialogue.turn("下一步怎么准备？", CasePatch(updates=[], ambiguities=[]))):
        assert result.case.profile.visit_purpose == "tourism"
        assert result.case.profile.funding_source == "self"
        assert not {"visit_purpose", "funding_source", "planned_arrival_date",
                    "planned_departure_date"}.intersection(result.case.last_requested_fields)
        assert "主要是旅游、探亲访友" not in result.body
