"""Delivery-backed memory for unfinished consultation, separate from intake.

Keep the original request and its qualifiers, not yesterday's rendered advice.
Neither a draft nor an uncertain send is evidence that a question was answered.
"""

import re
from datetime import date
from typing import Any

from visa_agent.domain.models import (
    AdviceAnswerAttempt,
    AdviceSourceQuestion,
    Case,
    PendingAdviceQuestion,
)
from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.conversation import latest_reply_text
from visa_agent.workflow.customer_questions import (
    APPLICATION_SOURCE,
    CHECKED_AT,
    ReviewedAnswerPlan,
    _active_clauses,
    capped_answer_plan,
    grounded_customer_answer_plan,
)


def is_advice_continuation(body: str) -> bool:
    """Only a whole, explicit information-continuation request bypasses intake.

    Generic 'continue' remains ambiguous. Do not discard a separate new question,
    fact, condition, negation, inline quote or preparation-control instruction.
    """
    text = re.sub(r"\s+", " ", latest_reply_text(body)).strip()
    return bool(re.fullmatch(
        r"(?:(?:好[的啊]?|可以|嗯)[，, ]*)?(?:那[，, ]*)?(?:(?:请|麻烦)(?:你)?)?"
        r"(?:(?:接着|继续)(?:讲|说)(?:一下)?|(?:剩下的|其余的)(?:问题|部分|内容)?呢|"
        r"(?:接着|继续)(?:讲|说|回答|解释|说明)(?:一下)?(?:刚才|之前|上次|上封邮件)?"
        r"(?:还)?(?:没(?:有)?(?:说|讲|答|回答)(?:完)?的|未(?:回答|讲完)的|剩下的|其余的)"
        r"(?:问题|部分|内容|咨询)?|"
        r"(?:刚才|之前|上次|上封邮件)?(?:还)?(?:没(?:有)?(?:讲|说|答|回答)完的|"
        r"没(?:有)?(?:讲|说|答|回答)的|剩下的|未回答的)(?:问题|部分|内容|咨询)?[，, ]*"
        r"(?:请)?(?:接着|继续)(?:讲|说|回答|解释|说明)(?:一下)?)"
        r"(?:吧|好吗|可以吗)?[。.!?？ ]*|"
        r"(?:(?:please|could you|can you|would you)\s+)?"
        r"(?:continue|carry on|go on)(?:\s+(?:with|answering|explaining))?\s+"
        r"(?:(?:the|my)\s+)?(?:remaining|unanswered|other)\s+(?:questions|topics|points)"
        r"(?:\s+(?:from (?:before|last time)|please))?[.?! ]*|"
        r"(?:(?:please|could you|can you)\s+)?(?:explain|answer|cover)\s+"
        r"(?:what|the (?:questions|topics|points))\s+you (?:haven't|have not|didn't|did not) "
        r"(?:covered|cover|answered|answer|explained|explain)(?:\s+yet)?[.?! ]*|"
        r"what about (?:the rest|the remaining (?:questions|topics|points))[.?! ]*",
        text, re.I,
    ))


def _sent_text(rows: list[dict[str, Any]], event_id: str, text: str) -> bool:
    # Match the rendering guard's complete-item, case-insensitive contract.
    # Do not remove qualifiers, prices, links or require only a topic keyword.
    return bool(text) and any(row["event_id"] == event_id and row["status"] == "SENT"
                              and text.casefold() in row["payload"].casefold() for row in rows)


def has_advice_continuation_request(body: str) -> bool:
    """A separate information request can coexist with facts or attachments.

    This recognizer never skips extraction or attachment processing. Keep a
    condition's scope across punctuation, and do not act for a reported speaker.
    """
    if re.search(r"如果|假如|假设|除非|\b(?:if|unless|assuming|suppose)\b|"
                 r"(?:朋友|客户|同学|他|她)(?:说|问|让我)|"
                 r"\b(?:friend|customer|client|he|she)\s+(?:said|asked|says|asks)\b", body, re.I):
        return False
    return any(is_advice_continuation(clause) for clause in _active_clauses(body))


def reconcile_answered_advice(case: Case, rows: list[dict[str, Any]]) -> None:
    """Called only with this case's outbox. Draft attempts are never consumed."""
    case.pending_advice = [item for item in case.pending_advice if not any(
        _sent_text(rows, attempt.event_id, attempt.answer) for attempt in item.answer_attempts
    )]
    case.unsent_advice = [item for item in case.unsent_advice if not any(
        _sent_text(rows, attempt.event_id, attempt.answer)
        for attempt in item.answer_attempts + item.omission_attempts
    )]


def _current_answer(item: PendingAdviceQuestion, language: str, today: date,
                    rows: list[dict[str, Any]] | None = None) -> str | None:
    # Validation is repeated by the compiler, against the full original body.
    questions = [CustomerQuestion.model_validate(question.model_dump()) for question in item.source_questions]
    sent_context = bool(item.source_application_guidance_event_id and any(
        row["event_id"] == item.source_application_guidance_event_id and row["status"] == "SENT"
        and APPLICATION_SOURCE in row["payload"] for row in rows or []
    ))
    plan = grounded_customer_answer_plan(item.source_body, language, today, semantic_questions=questions,
                                         sent_application_guidance=sent_context)
    matches = list(dict.fromkeys(answer for topic, answer in plan.reviewed_answers if topic == item.topic))
    if len(matches) == 1:
        return matches[0]
    if item.source_answer:
        return item.source_answer if item.source_answer in matches else None
    return matches[0] if matches else None


def remember_advice_plan(
    case: Case, event_id: str, body: str, questions: list[CustomerQuestion],
    plan: ReviewedAnswerPlan, rows: list[dict[str, Any]], today: date,
) -> None:
    # An explicit fresh question can also answer an older pending one, but only
    # when its actual reviewed answer covers the original, qualified request.
    for item in case.pending_advice:
        if not _sent_text(rows, item.notice_event_id or item.source_event_id, item.offered_notice):
            continue
        expected = _current_answer(item, case.customer_language, today, rows)
        if expected and any(expected in answer for answer in plan.answers):
            item.answer_attempts.append(AdviceAnswerAttempt(event_id=event_id, answer=expected))
    for topic in dict.fromkeys(plan.omitted_topics):
        if not any(item.source_event_id == event_id and item.topic == topic for item in case.pending_advice):
            case.pending_advice.append(PendingAdviceQuestion(
                topic=topic, source_event_id=event_id, source_body=body,
                source_questions=[AdviceSourceQuestion(**question.model_dump()) for question in questions],
                offered_notice=plan.omission_notice, source_checked_at=CHECKED_AT,
            ))


def continue_advice(case: Case, event_id: str, rows: list[dict[str, Any]], today: date) -> list[str]:
    """Regenerate from current reviewed sources; only register a send attempt."""
    from visa_agent.workflow.advice_queue import merge_unsent_advice

    answers = merge_unsent_advice(
        case, event_id, case.latest_customer_message, capped_answer_plan([], case.customer_language),
        rows, today, explicit=True,
    )
    if answers:
        return answers
    zh = case.customer_language == "zh"
    return [
            "我这里没有找到上一封已发回复中尚未展开的问题。你想接着了解哪一点？告诉我问题就好，不用重发个人资料。"
            if zh else
            "I can't find an outstanding question from the replies I've sent. Which point would you like to pick up? "
            "Just tell me the question; you don't need to resend your personal details."
    ]
