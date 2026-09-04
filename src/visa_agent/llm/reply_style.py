"""Closed-set social wording for replies whose substantive content is deterministic.

This module deliberately has no dependency on a case, customer message, policy, or
workflow state.  A model may propose only the enum-valued :class:`ReplyStylePlan`.
Deterministic code validates that proposal against typed signals and resolves the
accepted IDs through the fixed bilingual phrase catalogue below.

The returned fragments are not a complete customer reply.  They are optional social
bridges around separately reviewed answers, issues, documents, and questions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

Language = Literal["zh", "en"]


class OpeningVariant(StrEnum):
    NONE = "none"
    FIRST_CONTACT = "first_contact"
    FOLLOW_UP = "follow_up"
    CHANGE_RECEIVED = "change_received"


class TransitionVariant(StrEnum):
    NONE = "none"
    REVIEWED_ANSWER = "reviewed_answer"
    RECEIVED_DOCUMENTS = "received_documents"
    CURRENT_ISSUES = "current_issues"
    EXISTING_QUESTION = "existing_question"


class ClosingVariant(StrEnum):
    NONE = "none"
    REPLY_WHEN_CONVENIENT = "reply_when_convenient"
    ANSWER_IN_OWN_WORDS = "answer_in_own_words"
    FILES_RETAINED = "files_retained"
    CHANGES_CARRIED_FORWARD = "changes_carried_forward"


class ReplyStylePlan(BaseModel):
    """A model-proposable style choice with no free-text field."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    opening: OpeningVariant = OpeningVariant.NONE
    transition: TransitionVariant = TransitionVariant.NONE
    closing: ClosingVariant = ClosingVariant.NONE


class ReplyStyleSignals(BaseModel):
    """The complete, non-sensitive context visible to the style selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    language: Language
    is_follow_up: StrictBool
    has_reviewed_answers: StrictBool = False
    has_documents: StrictBool = False
    has_issues: StrictBool = False
    has_one_question: StrictBool = False
    has_changes: StrictBool = False


@dataclass(frozen=True)
class ReplyStyleFragments:
    """Resolved fixed text plus the normalized plan that authorized it."""

    plan: ReplyStylePlan
    opening: str
    transition: str
    closing: str

    @property
    def text(self) -> str:
        return "\n\n".join(
            fragment for fragment in (self.opening, self.transition, self.closing) if fragment
        )


_OPENING_PHRASES: dict[Language, dict[OpeningVariant, str]] = {
    "zh": {
        OpeningVariant.NONE: "",
        OpeningVariant.FIRST_CONTACT: "当然可以，我先按你这封邮件里的情况来梳理。",
        OpeningVariant.FOLLOW_UP: "明白，我们接着上次的进度来。",
        OpeningVariant.CHANGE_RECEIVED: "收到这次更新，后面的内容我会按最新情况来。",
    },
    "en": {
        OpeningVariant.NONE: "",
        OpeningVariant.FIRST_CONTACT: "Of course — I’ll start with what you’ve shared here.",
        OpeningVariant.FOLLOW_UP: "Got it — let’s pick up where we left off.",
        OpeningVariant.CHANGE_RECEIVED: (
            "Thanks for the update — I’ll use the latest information below."
        ),
    },
}

_TRANSITION_PHRASES: dict[Language, dict[TransitionVariant, str]] = {
    "zh": {
        TransitionVariant.NONE: "",
        TransitionVariant.REVIEWED_ANSWER: "先回答你刚才问的。",
        TransitionVariant.RECEIVED_DOCUMENTS: "你发来的文件，我会和前面的信息一起看。",
        TransitionVariant.CURRENT_ISSUES: "目前要留意的地方，我也放在下面。",
        TransitionVariant.EXISTING_QUESTION: "为了把下一步安排准一点，我只再确认一件事。",
    },
    "en": {
        TransitionVariant.NONE: "",
        TransitionVariant.REVIEWED_ANSWER: "First, to answer your question:",
        TransitionVariant.RECEIVED_DOCUMENTS: (
            "I’ll consider the files you sent alongside the earlier details."
        ),
        TransitionVariant.CURRENT_ISSUES: (
            "I’ve also set out the point to keep in view below."
        ),
        TransitionVariant.EXISTING_QUESTION: (
            "To tailor the next step, I just need to confirm one thing."
        ),
    },
}

_CLOSING_PHRASES: dict[Language, dict[ClosingVariant, str]] = {
    "zh": {
        ClosingVariant.NONE: "",
        ClosingVariant.REPLY_WHEN_CONVENIENT: "不着急，你方便时回复就好。",
        ClosingVariant.ANSWER_IN_OWN_WORDS: "按你现在知道的情况，用日常说法回复就好。",
        ClosingVariant.FILES_RETAINED: "收到的文件会继续留在这次整理记录里。",
        ClosingVariant.CHANGES_CARRIED_FORWARD: "接下来我会按这次更新后的信息继续。",
    },
    "en": {
        ClosingVariant.NONE: "",
        ClosingVariant.REPLY_WHEN_CONVENIENT: "No rush — reply when it suits you.",
        ClosingVariant.ANSWER_IN_OWN_WORDS: "Share what you know in your own words.",
        ClosingVariant.FILES_RETAINED: (
            "The files already received remain part of this preparation record."
        ),
        ClosingVariant.CHANGES_CARRIED_FORWARD: (
            "I’ll carry the updated information into the next reply."
        ),
    },
}


def normalize_reply_style_plan(
    proposed: ReplyStylePlan,
    signals: ReplyStyleSignals,
) -> ReplyStylePlan:
    """Drop every recognized choice that lacks the required typed context.

    Invalid enum values and extra fields are rejected by Pydantic before this
    function runs.  Contextually invalid but schema-valid choices fail closed to
    ``none`` rather than being reinterpreted as a different model instruction.
    """

    opening = proposed.opening
    opening_context = {
        OpeningVariant.NONE: True,
        OpeningVariant.FIRST_CONTACT: not signals.is_follow_up and not signals.has_changes,
        OpeningVariant.FOLLOW_UP: signals.is_follow_up and not signals.has_changes,
        OpeningVariant.CHANGE_RECEIVED: signals.has_changes,
    }
    if not opening_context[opening]:
        opening = OpeningVariant.NONE

    transition = proposed.transition
    transition_context = {
        TransitionVariant.NONE: True,
        TransitionVariant.REVIEWED_ANSWER: signals.has_reviewed_answers,
        TransitionVariant.RECEIVED_DOCUMENTS: signals.has_documents,
        TransitionVariant.CURRENT_ISSUES: signals.has_issues,
        TransitionVariant.EXISTING_QUESTION: signals.has_one_question,
    }
    if not transition_context[transition]:
        transition = TransitionVariant.NONE

    closing = proposed.closing
    closing_context = {
        ClosingVariant.NONE: True,
        ClosingVariant.REPLY_WHEN_CONVENIENT: True,
        ClosingVariant.ANSWER_IN_OWN_WORDS: signals.has_one_question,
        ClosingVariant.FILES_RETAINED: signals.has_documents,
        ClosingVariant.CHANGES_CARRIED_FORWARD: signals.has_changes,
    }
    if not closing_context[closing]:
        closing = ClosingVariant.NONE

    return ReplyStylePlan(opening=opening, transition=transition, closing=closing)


def safe_default_reply_style_plan(signals: ReplyStyleSignals) -> ReplyStylePlan:
    """Choose a deterministic plan when a model is absent or its proposal fails."""

    opening = (
        OpeningVariant.CHANGE_RECEIVED
        if signals.has_changes
        else OpeningVariant.FOLLOW_UP
        if signals.is_follow_up
        else OpeningVariant.FIRST_CONTACT
    )
    transition = (
        TransitionVariant.REVIEWED_ANSWER
        if signals.has_reviewed_answers
        else TransitionVariant.RECEIVED_DOCUMENTS
        if signals.has_documents
        else TransitionVariant.CURRENT_ISSUES
        if signals.has_issues
        else TransitionVariant.EXISTING_QUESTION
        if signals.has_one_question
        else TransitionVariant.NONE
    )
    closing = (
        ClosingVariant.ANSWER_IN_OWN_WORDS
        if signals.has_one_question
        else ClosingVariant.FILES_RETAINED
        if signals.has_documents
        else ClosingVariant.CHANGES_CARRIED_FORWARD
        if signals.has_changes
        else ClosingVariant.REPLY_WHEN_CONVENIENT
    )
    return ReplyStylePlan(opening=opening, transition=transition, closing=closing)


def render_reply_style(
    proposed: ReplyStylePlan,
    signals: ReplyStyleSignals,
) -> ReplyStyleFragments:
    """Resolve a normalized plan to fixed phrases without accepting customer text."""

    plan = normalize_reply_style_plan(proposed, signals)
    return ReplyStyleFragments(
        plan=plan,
        opening=_OPENING_PHRASES[signals.language][plan.opening],
        transition=_TRANSITION_PHRASES[signals.language][plan.transition],
        closing=_CLOSING_PHRASES[signals.language][plan.closing],
    )


def style_phrase_catalog() -> tuple[str, ...]:
    """Expose the immutable phrase values for invariant and release checks."""

    return tuple(
        phrase
        for catalogue in (_OPENING_PHRASES, _TRANSITION_PHRASES, _CLOSING_PHRASES)
        for language_phrases in catalogue.values()
        for phrase in language_phrases.values()
        if phrase
    )
