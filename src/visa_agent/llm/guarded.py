from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable

from pydantic import TypeAdapter, ValidationError

from visa_agent.domain.date_evidence import canonical_date_value, date_is_grounded, has_calendar_day
from visa_agent.domain.models import Case, CaseProfile, CaseStatus, InboundEvent
from visa_agent.llm.ports import CasePatch, FactUpdate, LLMClient
from visa_agent.workflow.conversation import (
    blocked_customer_message,
    change_acknowledgement,
    confirmation_message,
    latest_reply_text,
    preparation_control_receipt,
    reply_items,
    waiting_acknowledgement,
)
from visa_agent.workflow.customer_questions import validated_customer_questions
from visa_agent.workflow.preparation_control import validated_preparation_intent

MIN_ACCEPTED_CONFIDENCE = 0.8
MAX_MODEL_ATTEMPTS = 2
MAX_REPLY_CHARACTERS = 4_000
FORBIDDEN_REPLY_CLAIMS = (
    "保证获批",
    "保证通过",
    "一定获批",
    "签证已经批准",
    "已替你提交申请",
    "your visa is approved",
    "your application is approved",
    "you are eligible",
    "guaranteed success",
    "guarantee approval",
    "documents are sufficient for approval",
    "application has been submitted",
    "ready for approval",
    "sufficient for approval",
)
SPONSOR_RELATIONSHIPS = (
    "mother",
    "father",
    "sister",
    "brother",
    "spouse",
    "partner",
    "friend",
    "employer",
)


class UnsafeModelOutput(ValueError):
    pass


def _normalise_evidence(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _normalise_message_formatting(value: str) -> str:
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    return re.sub(r"`([^`]+)`", r"\1", value)


def _question_format_key(value: str) -> str:
    """Ignore prose punctuation only, retaining words and numeric punctuation."""
    value = unicodedata.normalize('NFKC', value).casefold()
    kept = []
    for index, char in enumerate(value):
        near_digit = (index > 0 and value[index - 1].isdigit()) or (
            index + 1 < len(value) and value[index + 1].isdigit())
        if not unicodedata.category(char).startswith('P') or near_digit:
            kept.append(char)
    return re.sub(r'\s+', ' ', ''.join(kept)).strip()


def _canonical_value(field: str, value: str | int | bool) -> str | int | bool:
    if field in {"date_of_birth", "planned_arrival_date", "planned_departure_date"} and isinstance(value, str):
        return canonical_date_value(value)
    if field == "sponsor_relationship" and isinstance(value, str):
        normalised = _normalise_evidence(value)
        matches = [item for item in SPONSOR_RELATIONSHIPS if item in normalised]
        if len(matches) == 1:
            return matches[0]
    return value


def validate_case_patch(event: InboundEvent, proposed: CasePatch) -> CasePatch:
    """Return only grounded, type-valid, non-conflicting candidate facts."""

    body = _normalise_evidence(event.body)
    accepted: dict[str, FactUpdate] = {}
    rejected_fields: set[str] = set()
    ambiguities = list(proposed.ambiguities)
    # A model cannot acknowledge unresolved ambiguity while allowing automatic progression.
    requires_review = proposed.requires_human_review or bool(proposed.ambiguities)

    for update in proposed.updates:
        update = update.model_copy(update={"value": _canonical_value(update.field, update.value)})
        if update.field == "sponsor_name" and re.fullmatch(
            r"(?:(?:my|the|our)\s+)?(?:mother|father|sister|brother|spouse|partner|friend|"
            r"employer|parent|sponsor|wife|husband)|(?:我的?|我们的?)?(?:母亲|父亲|妈妈|爸爸|"
            r"姐姐|妹妹|哥哥|弟弟|朋友|配偶|丈夫|妻子|雇主|资助人)",
            str(update.value).strip(),
            re.I,
        ):
            # A relationship is not a personal name; retain the missing-name question.
            continue
        is_date = update.field in {
            "planned_arrival_date",
            "planned_departure_date",
            "date_of_birth",
        }
        if is_date and not has_calendar_day(update.source_excerpt):
            # "November" is useful conversational context, not a precise date and
            # not a reason to lock a routine enquiry into human review.
            continue
        allow_shared_year = update.field != "date_of_birth"
        if is_date and not date_is_grounded(
            str(update.value), update.source_excerpt, allow_shared_year=allow_shared_year
        ):
            candidates = [
                sentence.strip()
                for sentence in re.split(r"[。！？!?；;]", event.body)
                if _normalise_evidence(update.source_excerpt) in _normalise_evidence(sentence)
                and date_is_grounded(str(update.value), sentence, allow_shared_year=allow_shared_year)
            ]
            if len(candidates) == 1:
                update = update.model_copy(update={"source_excerpt": candidates[0]})
        # A literal quote is necessary but not sufficient: employment/passport facts
        # must not silently become residential/application-location facts.
        cues = {
            "application_country": r"appl(?:y|ying|ication)|申请|递交|提交",
            "current_address": r"address|residen|live|living|住址|居住|住在|家在|地址",
            "nationality": r"passport|citizen|national|国籍|护照|公民|国人|\b(?:Chinese|British|American|Canadian|French|German|Indian|Australian)\b",
            "nationality_country": r"passport|citizen|national|国籍|护照|公民|国人|\b(?:Chinese|British|American|Canadian|French|German|Indian|Australian)\b",
        }
        if (
            update.field in cues
            and update.confidence >= MIN_ACCEPTED_CONFIDENCE
            and event.requested_fields != [update.field]
            and not re.search(cues[update.field], update.source_excerpt, re.I)
        ):
            # Leave the ordinary question open, rather than escalating routine missing data.
            continue
        reason: str | None = None
        field_info = CaseProfile.model_fields.get(update.field)
        if field_info is None:
            reason = f"Unsupported field proposed: {update.field}."
        elif (
            not update.source_excerpt.strip()
            or _normalise_evidence(update.source_excerpt) not in body
        ):
            reason = f"Evidence excerpt for {update.field} was not found in the inbound message."
        elif update.confidence < MIN_ACCEPTED_CONFIDENCE:
            reason = f"Low-confidence value proposed for {update.field}."
        elif is_date and not date_is_grounded(
            str(update.value), update.source_excerpt, allow_shared_year=allow_shared_year
        ):
            reason = f"Date value for {update.field} was not grounded in its excerpt."
        else:
            try:
                TypeAdapter(field_info.annotation).validate_python(update.value)
            except ValidationError:
                reason = f"Invalid value proposed for {update.field}."

        if reason is not None:
            ambiguities.append(reason)
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue

        prior = accepted.get(update.field)
        if prior is not None and prior.value != update.value:
            ambiguities.append(f"Conflicting values proposed for {update.field}.")
            rejected_fields.add(update.field)
            accepted.pop(update.field, None)
            requires_review = True
            continue
        if update.field not in rejected_fields:
            accepted[update.field] = update

    route_update = accepted.get("route_confirmed_standard_visitor")
    history_update = accepted.get("has_serious_history")
    if (route_update is not None and route_update.value is False) or (
        history_update is not None and history_update.value is True
    ):
        requires_review = True

    return CasePatch(
        updates=list(accepted.values()),
        ambiguities=list(dict.fromkeys(ambiguities)),
        requires_human_review=requires_review,
        question_deferrals=[item for item in proposed.question_deferrals
            if item.confidence >= MIN_ACCEPTED_CONFIDENCE
            and item.source_excerpt.strip()
            and _normalise_evidence(item.source_excerpt) in _normalise_evidence(latest_reply_text(event.body))],
        customer_questions=validated_customer_questions(event.body, proposed.customer_questions),
        preparation_intent=validated_preparation_intent(event.body, proposed.preparation_intent),
    )


def deterministic_fallback_message(case: Case, plan: str) -> str:
    if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
        message = (
            "这部分我还不能可靠判断，需要人工核实后才能继续，不能直接给你确定答复。你发来的信息和文件都已保留，暂时不用重新发送。"
            if case.customer_language == "zh"
            else "Thank you for explaining. This needs a human adviser to check before we continue. Your information is retained; you don't need to resend it. I haven't prepared or submitted an application."
        )
        history_reported = (case.profile.has_serious_history is True and "has_serious_history" in
                            (set(case.latest_changes) | set(case.latest_received_facts)))
        if history_reported:
            message = (
                "你补充的拒签或其他重要经历已记下，需要人工顾问结合相关记录复核，才能判断对这次申请的影响。"
                "目前先不定稿，已经收到的资料不用重发。"
                if case.customer_language == "zh" else
                "I've recorded the refusal or other significant history you've disclosed. "
                "A human adviser needs to check the relevant records before assessing its implications "
                "for this application. We won't finalise the pack yet; you don't need to resend the details already received."
            )
        acknowledgement = change_acknowledgement(case.model_copy(update={"latest_changes": {
            key: value for key, value in case.latest_changes.items() if key != "has_serious_history"
        }}))
        receipt = preparation_control_receipt(case)
        return "\n\n".join([*([acknowledgement] if acknowledgement else []),
                            *([receipt] if receipt else []), message, *case.customer_answers])
    if plan == "blocked":
        return blocked_customer_message(case)
    if plan == "ready":
        if case.next_step_advice is not None:
            receipt_case = case.model_copy(update={"next_step_advice": None, "customer_answers": []})
            return "\n\n".join([*case.customer_answers, deterministic_fallback_message(receipt_case, plan)])
        if case.delivery_revision > 1:
            return (
                f"已按你重新确认的信息整理成第 {case.delivery_revision} 版材料包，供顾问复核。"
                "请以这一版为准，并检查说明和信息摘要；旧版可能仍保留在之前的邮件中，不能自动撤回。"
                "这里完成的是材料修订，还没有递交或修改政府系统里的签证申请，也不代表获批。"
                if case.customer_language == "zh" else
                f"Revision {case.delivery_revision} of your preparation pack reflects your newly confirmed information "
                "and is ready for adviser review. Please use this version; any copy already sent remains in your "
                "previous email and cannot be recalled automatically. No government application has been "
                "submitted or amended, and this is not an approval prediction."
            )
        if case.customer_language == "zh":
            return "你的申请资料已整理好，供顾问复核。建议先看里面的说明和信息摘要，再逐项核对文件；如果有遗漏或需要修改的地方，直接回复告诉我。这里完成的是材料整理，还没有递交签证申请，也不代表签证获批。"
        return (
            "Your review pack is ready for human review. This is not an approval prediction or a "
            "submitted visa application."
        )
    return confirmation_message(case, profile_only=plan == "awaiting_profile_confirmation")


class GuardedLLM:
    """Mandatory safety boundary around an interchangeable model adapter."""

    def __init__(
        self,
        delegate: LLMClient,
        *,
        max_attempts: int = MAX_MODEL_ATTEMPTS,
        on_failure: Callable[[str, Exception], None] | None = None,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        self.delegate = delegate
        self.max_attempts = max_attempts
        self.on_failure = on_failure
        self.version = f"guarded:{getattr(delegate, 'version', 'unknown')}"
        self.last_extraction_fallback = False
        self.last_extraction_error: str | None = None
        self.last_render_fallback = False
        self.last_render_error: str | None = None

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        last_error: Exception | None = None
        for _ in range(self.max_attempts):
            try:
                patch = validate_case_patch(event, self.delegate.extract_case_patch(event))
                if any(
                    reason.startswith(("Evidence excerpt for ", "Date value for "))
                    for reason in patch.ambiguities
                ):
                    raise UnsafeModelOutput("; ".join(patch.ambiguities))
                self.last_extraction_fallback = False
                self.last_extraction_error = None
                return patch
            except Exception as error:  # Provider/SDK failures must not mutate or lose the case.
                last_error = error
        assert last_error is not None
        self._report("extract_case_patch", last_error)
        self.last_extraction_fallback = True
        self.last_extraction_error = str(last_error)
        return CasePatch(
            updates=[],
            ambiguities=["Automated extraction was unavailable; manual review is required."],
            requires_human_review=True,
        )

    def render_message(self, case: Case, plan: str) -> str:
        if case.status == CaseStatus.HUMAN_REVIEW_REQUIRED:
            self.last_render_fallback = True
            self.last_render_error = "case_requires_human_review"
            return deterministic_fallback_message(case, plan)
        if case.preparation_paused:
            # The drafting model cannot restart intake or confirmation while paused.
            self.last_render_fallback = False
            self.last_render_error = None
            return blocked_customer_message(case)
        if case.next_step_advice is not None:
            # Case-aware next steps and accompanying FAQs must survive wording.
            self.last_render_fallback = False
            self.last_render_error = None
            return deterministic_fallback_message(case, plan)
        if plan == "blocked" and (acknowledgement := waiting_acknowledgement(case)):
            self.last_render_fallback = False
            self.last_render_error = None
            return acknowledgement
        if plan == "blocked" and case.question_plan == [] and case.pending_question_fields:
            # An unanswered question is not permission for the wording model to ask it again.
            self.last_render_fallback = False
            self.last_render_error = None
            return deterministic_fallback_message(case, plan)
        if plan in {"awaiting_confirmation", "awaiting_profile_confirmation"}:
            self.last_render_fallback = False
            self.last_render_error = None
            return confirmation_message(case, profile_only=plan == "awaiting_profile_confirmation")
        try:
            message = _normalise_message_formatting(
                self.delegate.render_message(case, plan).strip()
            )
            if not message:
                raise ValueError("Model returned an empty message")
            if len(message) > MAX_REPLY_CHARACTERS:
                raise UnsafeModelOutput("Model message exceeded the configured length limit")
            normalised = message.casefold()
            if any(claim in normalised for claim in FORBIDDEN_REPLY_CLAIMS):
                raise UnsafeModelOutput("Model message contained a prohibited outcome claim")
            if any(
                placeholder in normalised
                for placeholder in ("[name]", "[applicant name]", "[your name]")
            ):
                raise UnsafeModelOutput("Model message contained an unresolved placeholder")
            if plan == "blocked":
                issues, questions, documents = reply_items(case)
                exact_items = case.customer_answers + issues + documents
                if acknowledgement := change_acknowledgement(case):
                    exact_items.append(acknowledgement)
                required_items = exact_items + questions
                if (any(item.casefold() not in normalised for item in exact_items)
                        or any(_question_format_key(item) not in _question_format_key(message)
                               for item in questions)):
                    raise UnsafeModelOutput(
                        "Model message omitted or changed a grounded next action"
                    )
                if re.search(
                    r"no documents (?:are )?(?:needed|required)|"
                    r"hold off on any further steps|won['’]t move forward with anything|"
                    r"(?:不需要|不用)(?:任何)?(?:文件|材料)", normalised,
                ):
                    raise UnsafeModelOutput("Model added an unsupported preparation waiver or global pause")
                length_budget = max(
                    420 if case.customer_language == "zh" else 1100,
                    len("\n".join(required_items)) + 180,
                )
                if len(message) > length_budget:
                    raise UnsafeModelOutput("Model buried the next action in excessive prose")
                if any(
                    phrase in normalised
                    for phrase in (
                        "没有现成的标准答案",
                        "不能给你一个确切的步骤清单",
                        "no standard answer",
                    )
                ):
                    raise UnsafeModelOutput(
                        "Model denied a preparation step already supplied in its brief"
                    )
                if re.search(
                    r"(?:时间|日期).{0,12}(?:没问题|没有问题|来得及)|\benough time\b|\bdates (?:are|look) (?:fine|acceptable)\b",
                    normalised,
                ):
                    raise UnsafeModelOutput("Model added an unsupported timing assurance")
                if case.customer_language == "zh" and not re.search(r"[\u4e00-\u9fff]", message):
                    raise UnsafeModelOutput("Model ignored the customer's language")
            if plan == "awaiting_confirmation" and "i confirm the final summary" not in normalised:
                raise UnsafeModelOutput("Model message omitted the exact confirmation statement")
            if plan == "awaiting_confirmation" and any(
                claim in normalised
                for claim in ("pack is ready", "pack has been prepared", "pack is released")
            ):
                raise UnsafeModelOutput("Model message claimed release before confirmation")
            if plan == "ready" and not (
                ("human" in normalised and "review" in normalised)
                if case.customer_language != "zh"
                else "顾问复核" in message
            ):
                raise UnsafeModelOutput("Model message omitted the human-review boundary")
            self.last_render_fallback = False
            self.last_render_error = None
            return message
        except Exception as error:
            self._report("render_message", error)
            self.last_render_fallback = True
            self.last_render_error = f"{type(error).__name__}: {str(error)[:160]}"
            return deterministic_fallback_message(case, plan)

    def _report(self, operation: str, error: Exception) -> None:
        if self.on_failure is not None:
            self.on_failure(operation, error)


def ensure_guarded(llm: LLMClient) -> GuardedLLM:
    if isinstance(llm, GuardedLLM):
        return llm
    return GuardedLLM(llm)
