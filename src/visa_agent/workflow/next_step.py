"""One case-aware preparation suggestion, never preference, consent or release authority."""

from __future__ import annotations

import re

from visa_agent.domain.models import (
    Case,
    CaseStatus,
    DocumentStatus,
    GateResult,
    NextStepAdvice,
    Requirement,
)
from visa_agent.domain.policy import Policy
from visa_agent.workflow.conversation import (
    explained_document_label,
    fact_label,
    latest_reply_text,
    next_fact_questions,
)
from visa_agent.workflow.preparation_obstacles import reviewed_obstacle_next_step

_DETAILS_EN = {
    "full_name": "the name in your passport", "date_of_birth": "your date of birth",
    "nationality_country": "which country's passport you hold", "application_country": "where you will apply",
    "visit_purpose": "the reason for your visit", "occupation_status": "your work or study circumstances",
    "funding_source": "who will pay for the trip", "uk_accommodation": "your intended accommodation",
    "estimated_trip_cost_gbp": "your approximate trip budget", "annual_income_gbp": "your annual income, if any",
    "current_address": "your current home address", "route_confirmed_standard_visitor": "the appropriate visa route",
    "sponsor_name": "your sponsor's name", "sponsor_relationship": "your relationship with your sponsor",
    "sponsor_is_in_uk": "whether your sponsor lives in the UK",
    "has_serious_history": "any relevant refusal or immigration history",
    "planned_arrival_date": "your intended arrival date", "planned_departure_date": "your intended departure date",
}


def _review_message(case: Case, policy: Policy, gate: GateResult) -> str | None:
    zh = case.customer_language == "zh"
    if gate.checks.get("policy_snapshot_is_current") is False:
        return ("下一步需要先复核最新官方要求，不能按过期的材料规则继续安排。" if zh else
                "The next step is to recheck the current official requirements before relying on older preparation rules.")
    if any(value is not None and value not in policy.scope[key] for key, value in (
        ("purposes", case.profile.visit_purpose), ("occupations", case.profile.occupation_status),
        ("funding", case.profile.funding_source),
    )):
        return ("下一步需要先由顾问核对适用路线，不能直接套用这份 Standard Visitor 材料安排。" if zh else
                "The next step is an adviser check of the appropriate route before applying this Standard Visitor preparation plan.")
    nationality = " ".join(value for value in (
        case.profile.nationality, case.profile.nationality_country,
    ) if value).casefold()
    if "british" in nationality or nationality in {"uk", "united kingdom"}:
        return ("下一步先请顾问核对你的国籍和适用入境安排，不能直接按这份访客签证清单准备。" if zh else
                "The next step is an adviser check of nationality and the appropriate entry arrangements before using this visitor-visa checklist.")
    if (case.status == CaseStatus.HUMAN_REVIEW_REQUIRED or case.human_review_reason
            or gate.checks.get("all_held_updates_reviewed") is False
            or case.profile.has_serious_history is True
            or any(document.status == DocumentStatus.HUMAN_REVIEW_REQUIRED for document in case.documents)):
        return ("下一步先核对已经标出的资料问题或待复核更新，不能跳过这一步继续定稿；已收到的文件不用重发。" if zh else
                "The next step is to review the flagged information or retained updates before finalising anything. "
                "There is no need to resend files already received.")
    if case.open_blockers():
        # The existing issue renderer retains the concrete correction/replacement
        # instructions; do not hide them behind a generic no-resend receipt.
        return ("下一步先处理下面已经指出的问题，不再另加一份材料要求。" if zh else
                "The next step is to address the issues set out below, rather than add another document request.")
    replacement = next((document for document in case.documents
                        if document.status == DocumentStatus.NEEDS_REPLACEMENT), None)
    if replacement is not None:
        return (f"下一步先为 {replacement.filename} 提供清晰可读的 PDF 替换件，再核对其中内容。" if zh else
                f"The next step is to provide a clear, readable PDF replacement for {replacement.filename}, then check its contents.")
    if any(document.status == DocumentStatus.NEEDS_CLARIFICATION for document in case.documents):
        return ("下一步先核对已收到文件中尚不清楚的内容，再判断是否需要其他材料。" if zh else
                "The next step is to clarify the outstanding content in the files already received before choosing further material.")
    if case.profile.date_of_birth is not None and gate.checks.get("applicant_age_at_least_18") is False:
        return ("按目前记录的出生日期，需要先请顾问核对年龄和未成年人的申请安排，再确定材料。" if zh else
                "The date of birth on file needs an adviser check of the applicant's age and arrangements for a child applicant before choosing documents.")
    if (case.profile.planned_arrival_date is not None and case.profile.planned_departure_date is not None
            and gate.checks.get("travel_dates_are_valid_and_within_six_months") is False):
        return (f"先核对这段行程：{case.profile.planned_arrival_date} 至 {case.profile.planned_departure_date}。"
                "目前日期未通过检查，需要确认前后顺序、是否已过期及停留时长，再继续安排材料。" if zh else
                f"Let's check the travel dates on file: {case.profile.planned_arrival_date} to {case.profile.planned_departure_date}. "
                "They need checking for date order, past dates and length of stay before arranging further material.")
    if (case.profile.planned_departure_date is not None
            and gate.checks.get("passport_valid_through_stay") is False and any(
        document.kind in {"passport", "travel_document"}
        and document.status == DocumentStatus.ACCEPTED_FOR_REVIEW for document in case.documents
    )):
        return ("下一步先核对已提供护照的有效期是否覆盖计划停留；已有文件不用重发。" if zh else
                "The next step is to check whether the passport already provided remains valid throughout the planned stay; "
                "there is no need to resend it.")
    return None


def _document_message(case: Case, item: Requirement, policy: Policy) -> str:
    zh = case.customer_language == "zh"
    explanation = explained_document_label(case, item)
    message = (f"下一步可以先准备这一份：{explanation}。" if zh else
               f"For the next item, start with {explanation}.")
    if item.id == "passport":
        message += ("把护照资料页扫描成清晰、完整的 PDF，确保文字和页边没有被裁掉。" if zh else
                    " Scan the passport details page into a clear, complete PDF without cropping its text or edges.")
    elif item.id == "funding_evidence" and case.profile.funding_source == "self":
        message += ("可以从网银下载正式对账单，找不到时向银行索取；不要只发余额截图。" if zh else
                    " Obtain official statements through online banking or ask your bank for them; do not send only a balance screenshot.")
    if case.primary_channel == "gmail":
        message += ("拿到后可以直接回复这封邮件，附上清晰可读的 PDF；我会先核对内容，再告诉你是否需要补充。" if zh else
                    " When you have it, reply with a clear, readable PDF attachment. I'll check its contents and let you know if anything else is needed.")
    elif item.id != "passport":
        message += ("拿到后整理成清晰可读的 PDF，方便核对内容。" if zh else
                    " Keep a clear, readable PDF so its contents can be checked.")
    rule = next((rule for rule in policy.requirements if rule.id == item.id), None)
    text = latest_reply_text(case.latest_customer_message)
    no_links = re.search(
        r"(?:不要|不用|无需|不需要|别).{0,12}(?:链接|网址|网站|官网)|"
        r"(?:don't|do not|no need|without).{0,20}(?:links?|websites?|URLs?)|\bno links?\b", text, re.I,
    )
    if rule is not None and not no_links:
        message += "\nGOV.UK: " + rule.source_url
    return message


def select_next_step(case: Case, policy: Policy, gate: GateResult) -> NextStepAdvice:
    """Read a freshly evaluated case; the caller validates the current request separately.

    The returned question field is for the existing question plan/SENT ledger, not
    a fact update. Paused previews never return a question field or request files.
    Nothing here changes requirements, document status, consent, epoch or stage.
    """
    zh = case.customer_language == "zh"
    review = _review_message(case, policy, gate)
    if review:
        if case.preparation_paused:
            review = ("准备保持暂停。如果之后决定恢复，仍需先处理现有的待核对事项，再决定材料安排；现在不用回答或补交材料。" if zh else
                      "Preparation remains on hold. If you later restart, the outstanding checks must come before choosing further preparation steps; "
                      "there is no need to answer or send anything now.")
        return NextStepAdvice(message=review, kind="review")
    if case.status != CaseStatus.DRAFT:
        return NextStepAdvice(message=(
            "这份档案已进入复核或交付阶段；下一步由顾问核对当前版本，不能直接重开或重发材料包。" if zh else
            "This case is already in review or delivery. The next step is an adviser check of the current version, "
            "not automatically reopening or resending a pack."
        ), kind="review")

    if not case.preparation_paused and gate.allowed and case.final_summary_confirmed:
        return NextStepAdvice(message=(
            "接下来按已经确认的版本整理材料包，供顾问复核；正式申请仍由你在官网提交。" if zh else
            "The next stage is assembling your confirmed details into a preparation pack for adviser review; "
            "you still submit the official application yourself on the official website."
        ), kind="waiting")
    if not case.preparation_paused and case.confirmation_kind in {"profile", "final"}:
        return NextStepAdvice(message=(
            "下一步先核对下面这份资料摘要，尤其是姓名、日期和费用由谁承担；有不准确的地方，直接指出来。" if zh else
            "The next step is to check the summary below, especially your name, dates and who pays for the trip. "
            "Point out anything that needs correcting."
        ), kind="waiting")

    obstacle_step = reviewed_obstacle_next_step(case, policy, gate)
    if obstacle_step is not None:
        return obstacle_step

    # Ignore this turn's prior pacing selection while preserving every fact and
    # date deferral. This shallow copy is read-only; no nested data is modified.
    candidates = next_fact_questions(case.model_copy(update={
        "preparation_paused": False, "question_plan": None,
    }))
    if candidates:
        field = candidates[0]
        if case.preparation_paused:
            label = fact_label(case, field) if zh else _DETAILS_EN.get(field, "the remaining personal details")
            return NextStepAdvice(message=(
                f"如果之后决定继续，下一步可以先核对还缺的{label}。现在不用回答或补交材料，准备仍保持暂停。" if zh else
                f"If you later decide to continue, the next detail to establish is {label}. "
                "There is no need to answer or send documents now; preparation remains on hold."
            ), kind="paused")
        return NextStepAdvice(message=(
            "下一步先补一项还缺的信息，便于按你的实际情况准备。" if zh else
            "Let's establish one missing detail next so the preparation matches your circumstances."
        ), kind="question", question_field=field)

    if gate.checks.get("route_in_scope") is False:
        return NextStepAdvice(message=(
            "下一步需要先由顾问核对适用路线，不能直接套用这份 Standard Visitor 材料安排。" if zh else
            "The next step is an adviser check of the appropriate route before applying this Standard Visitor preparation plan."
        ), kind="review")

    rules = {rule.id: rule for rule in policy.requirements}
    outstanding = [item for item in case.requirements if item.applicable and item.blocker and not item.satisfied]
    for item in outstanding:
        rule = rules.get(item.id)
        if rule is None or item.rule_version != policy.version:
            return NextStepAdvice(message=(
                "下一步需要先核对这项材料要求的依据，再确定该准备什么。" if zh else
                "The next step is to verify the basis for the outstanding material requirement before choosing a document."
            ), kind="review")
        pending = [document for document in case.documents
                   if document.kind in rule.acceptable_evidence
                   and document.status in {DocumentStatus.RECEIVED, DocumentStatus.PROCESSING}]
        if pending:
            message = (
                "这一项的文件已经收到，下一步先核对现有文件，不用再发一份。" if zh else
                "A file for this item has already been received. The next step is to check that file, not send another copy."
            )
            if case.preparation_paused:
                message = ("准备保持暂停。如果之后决定恢复，先核对这一项已收到的文件，现在不用再发一份。" if zh else
                           "Preparation remains on hold. If you later restart, the next step is to check the file already received "
                           "for this item; there is no need to send another copy now.")
            return NextStepAdvice(message=message,
                kind="paused" if case.preparation_paused else "waiting", requirement_id=item.id)
        if case.preparation_paused:
            return NextStepAdvice(message=(
                "如果之后决定继续，可以先参考这一项：" + explained_document_label(case, item)
                + "。这只是之后准备的说明，现在不用提交，准备仍保持暂停。" if zh else
                "For a later restart, the next item would be " + explained_document_label(case, item)
                + ". This is information for later, not a request to send it now; preparation remains on hold."
            ), kind="paused", requirement_id=item.id)
        return NextStepAdvice(message=_document_message(case, item, policy), kind="document", requirement_id=item.id)

    if case.preparation_paused:
        return NextStepAdvice(message=(
            "准备仍保持暂停。如果之后决定继续，需要再核对届时的资料摘要；现在不用确认，也不会因此发送材料包。" if zh else
            "Preparation remains on hold. If you later restart, the then-current summary will need checking; "
            "there is no need to confirm it now, and this does not send a pack."
        ), kind="paused")
    return NextStepAdvice(message=(
        "目前没有另一项待补材料可列。接下来核对当前资料摘要和仍未完成的检查；未确定的行程日期，可以等确定后再补。" if zh else
        "There is no further outstanding document to list at present. Next, check the current summary and any remaining checks; "
        "travel dates that are still undecided can be added when known."
    ), kind="waiting")
