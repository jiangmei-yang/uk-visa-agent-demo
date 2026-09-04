"""Carry actual unanswered applicant questions into the next deliverable reply.

This is intake evidence, not evidence of an earlier sent explanation. Regenerate
from reviewed sources and retain qualifiers; never concatenate old model drafts.
"""

import re
from datetime import date
from typing import Any

from visa_agent.domain.models import (
    AdviceAnswerAttempt,
    AdviceSourceQuestion,
    Case,
    PendingAdviceQuestion,
    UnsentAdviceQuestion,
)
from visa_agent.llm.ports import CustomerQuestion
from visa_agent.workflow.advice_continuation import _current_answer, _sent_text
from visa_agent.workflow.advice_preferences import (
    defer_previous_advice,
    excluded_advice_topics,
    route_change_pending,
    wants_no_links,
)
from visa_agent.workflow.customer_questions import (
    CHECKED_AT,
    REVIEW_AFTER,
    ReviewedAnswerPlan,
    _question_clauses,
    capped_answer_plan,
)


def _same_request(left: PendingAdviceQuestion, right: PendingAdviceQuestion) -> bool:
    return (left.source_event_id == right.source_event_id and left.topic == right.topic
            and left.source_body == right.source_body
            and (left.source_answer is None or right.source_answer is None
                 or left.source_answer == right.source_answer))


def queue_advice(case: Case, event_id: str, body: str, questions: list[CustomerQuestion],
                 plan: ReviewedAnswerPlan, *, application_guidance_event_id: str | None = None) -> None:
    # A label attached to quoted words or an intention to ask later is not a
    # question actually asked now. Do not manufacture an ongoing request from it.
    active = [clause for clause in _question_clauses(body) if not re.search(
        r"\b(?:I|we)\s+(?:might|may|will)\s+ask\b|(?:以后|将来|到时).{0,8}(?:再问|会问)|"
        r"\b(?:my friend|he|she|the customer)\s+(?:asked|said|wrote)\b|"
        r"(?:朋友|客户|他|她)(?:说|问|写道)", clause, re.I,
    )]
    if not active:
        return
    for topic, answer in plan.reviewed_answers:
        if topic == "off_topic":
            continue  # Do not keep repeating an out-of-service scope receipt.
        if any(item.source_event_id == event_id and item.topic == topic and item.source_answer == answer
               for item in case.unsent_advice):
            continue
        case.unsent_advice.append(UnsentAdviceQuestion(
            topic=topic, source_event_id=event_id, source_body=body,
            source_questions=[AdviceSourceQuestion(**item.model_dump()) for item in questions],
            offered_notice="", source_checked_at=CHECKED_AT, source_answer=answer,
            source_application_guidance_event_id=application_guidance_event_id,
        ))


def _uncertain(item: PendingAdviceQuestion, rows: list[dict[str, Any]]) -> bool:
    identifiers = {item.source_event_id, *(attempt.event_id for attempt in item.answer_attempts)}
    if isinstance(item, UnsentAdviceQuestion):
        identifiers.update(attempt.event_id for attempt in item.omission_attempts)
    if item.notice_event_id:
        identifiers.add(item.notice_event_id)
    return any(row["event_id"] in identifiers and row["status"] in {"SENDING", "AMBIGUOUS"} for row in rows)


def apply_current_format(answer: str, body: str) -> str:
    if not wants_no_links(body):
        return answer
    return re.sub(r"(?m)^[ \t]*GOV\.UK:[^\n]*(?:\n|$)", "", answer).strip().replace(
        "下面的 GOV.UK 页面", "GOV.UK 官方申请页面").replace(
        "the GOV.UK page below", "the official GOV.UK application page")


def merge_unsent_advice(case: Case, event_id: str, body: str, current: ReviewedAnswerPlan,
                        rows: list[dict[str, Any]], today: date, *, explicit: bool = False) -> list[str]:
    """Fresh questions first; cap the combined reply and bind any omission notice.

    An uncertain previous send is not a definitely-unsent answer. Leave it for
    normal send reconciliation, rather than copying it into another email.
    """
    excluded = excluded_advice_topics(body)
    defer_old = defer_previous_advice(body) or route_change_pending(body)
    item: PendingAdviceQuestion
    for item in [*case.unsent_advice, *case.pending_advice]:
        if item.topic in excluded or (defer_old and item.source_event_id != event_id):
            item.deferred_by_event_id = event_id
    active = [item for item in case.unsent_advice if not item.deferred_by_event_id
              and (item.source_event_id == event_id or explicit or not case.preparation_paused)]
    active.sort(key=lambda item: item.source_event_id != event_id)
    offered = [item for item in case.pending_advice if explicit and not item.deferred_by_event_id
               and _sent_text(rows, item.notice_event_id or item.source_event_id, item.offered_notice)]
    candidates: list[tuple[PendingAdviceQuestion, str]] = []
    unresolved, uncertain = False, False
    for item in [*active, *offered]:
        if _uncertain(item, rows):
            uncertain = True
            continue
        answer = (item.source_answer if item.source_event_id == event_id
                  else _current_answer(item, case.customer_language, today, rows))
        if not CHECKED_AT <= today <= REVIEW_AFTER or not answer:
            unresolved = unresolved or item.source_event_id != event_id
            continue
        candidates.append((item, apply_current_format(answer, body)))
    # Preserve the current compiler's boundary answers and order even when they
    # are intentionally not safe to regenerate as an old FAQ (e.g. expired data).
    pairs = [(topic, apply_current_format(answer, body)) for topic, answer in current.reviewed_answers
             if topic not in excluded]
    pairs.extend((item.topic, answer) for item, answer in candidates if item.source_event_id != event_id)
    if explicit and not pairs:
        pairs = [(item.topic, answer) for item, answer in candidates]
    plan = capped_answer_plan(pairs, case.customer_language)
    for item, answer in candidates:
        if any(answer.casefold() in text.casefold() for text in plan.answers):
            attempt = AdviceAnswerAttempt(event_id=event_id, answer=answer)
            if attempt not in item.answer_attempts:
                item.answer_attempts.append(attempt)
            for previous in case.pending_advice:
                if _same_request(item, previous) and attempt not in previous.answer_attempts:
                    previous.answer_attempts.append(attempt)
        elif plan.omission_notice:
            notice = AdviceAnswerAttempt(event_id=event_id, answer=plan.omission_notice)
            if isinstance(item, UnsentAdviceQuestion) and notice not in item.omission_attempts:
                item.omission_attempts.append(notice)
            pending = next((old for old in case.pending_advice if _same_request(item, old)
                            and old.source_answer in {None, item.source_answer}), None)
            if pending is None:
                pending = PendingAdviceQuestion(**item.model_dump(exclude={"omission_attempts"}))
                case.pending_advice.append(pending)
            pending.offered_notice = plan.omission_notice
            pending.notice_event_id = event_id
            pending.source_answer = item.source_answer
    answers = plan.answers
    if unresolved:
        answers.append("之前的问题还记着，但相关官方说明需要重新核实，这部分先不沿用旧答复。"
                       if case.customer_language == "zh" else
                       "I've kept the earlier questions, but their official guidance needs rechecking before I answer them.")
    if uncertain and explicit:
        answers.append("之前一封回复是否发送成功还未核实，我先不重复发送其中的答案。"
                       if case.customer_language == "zh" else
                       "The sending status of an earlier reply still needs checking; I won't duplicate its answers meanwhile.")
    return answers
