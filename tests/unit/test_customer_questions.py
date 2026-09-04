from datetime import date

from visa_agent.workflow.customer_questions import SOURCE, grounded_customer_answers


def test_booking_answer_has_reviewed_source_and_transit_exception() -> None:
    answers = grounded_customer_answers("我必须先买机票、订酒店吗？", "zh", date(2026, 9, 4))
    assert len(answers) == 1
    assert SOURCE in answers[0]
    assert "过境除外" in answers[0]
    assert "尚未确定" in answers[0]


def test_stale_advice_and_transit_are_not_given_standard_visitor_answer() -> None:
    assert "复核" in grounded_customer_answers("必须买机票吗？", "zh", date(2026, 11, 1))[0]
    assert "路线" in grounded_customer_answers("我过境需要买机票吗？", "zh", date(2026, 9, 4))[0]
    assert grounded_customer_answers("Here is my student letter.", "en", date(2026, 9, 4)) == []
