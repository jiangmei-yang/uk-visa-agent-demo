"""Useful preparation while an already-sent profile question remains open."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from visa_agent.domain.models import Case, DocumentStatus
from visa_agent.domain.policy import Policy
from visa_agent.workflow.advice_preferences import wants_no_links

_UNUSABLE_DOCUMENT_STATUSES = {
    DocumentStatus.NEEDS_REPLACEMENT,
    DocumentStatus.SUPERSEDED,
    DocumentStatus.NOT_APPLICABLE,
}


def _has_usable_document(case: Case, kinds: Iterable[str]) -> bool:
    accepted = set(kinds)
    return any(
        document.kind in accepted and document.status not in _UNUSABLE_DOCUMENT_STATUSES
        for document in case.documents
    )


def _source(policy: Policy, requirement_id: str, today: date, *, include: bool) -> str:
    if not include or not policy.is_current(today):
        return ""
    rule = next((item for item in policy.requirements if item.id == requirement_id), None)
    return "" if rule is None else f"\nGOV.UK: {rule.source_url}"


def pending_question_reminder(case: Case) -> str:
    """Refer to the open item without restating it as another question."""
    fields = case.pending_question_fields or case.last_requested_fields
    field = fields[0] if fields else ""
    labels_zh = {
        "full_name": "护照姓名",
        "date_of_birth": "出生日期",
        "nationality_country": "护照国家或地区",
        "application_country": "递交地点",
        "occupation_status": "目前的工作或学习情况",
        "funding_source": "费用由谁承担",
        "sponsor_relationship": "资助人关系和姓名",
        "sponsor_name": "资助人姓名",
        "uk_accommodation": "英国住宿安排",
        "estimated_trip_cost_gbp": "旅行预算",
        "annual_income_gbp": "目前的收入情况",
        "current_address": "现居住地址",
    }
    labels_en = {
        "full_name": "passport name",
        "date_of_birth": "date of birth",
        "nationality_country": "passport country",
        "application_country": "place of application",
        "occupation_status": "current work or study circumstances",
        "funding_source": "trip funding arrangement",
        "sponsor_relationship": "sponsor relationship and name",
        "sponsor_name": "sponsor name",
        "uk_accommodation": "UK accommodation plan",
        "estimated_trip_cost_gbp": "trip budget",
        "annual_income_gbp": "current income",
        "current_address": "home address",
    }
    if case.customer_language == "zh":
        label = labels_zh.get(field, "待补信息")
        return (
            f"上一封邮件里问的{label}还没有收到；你方便时在同一邮件里回复即可。"
            "我先不重复提问，前面已经记录的资料都会保留。"
        )
    label = labels_en.get(field, "outstanding detail")
    return (
        f"I still need the {label} asked for in my previous email. Reply in the same thread when convenient; "
        "I will not repeat the question here, and the details already recorded will remain on the case."
    )


def pending_question_support_action(
    case: Case,
    pending_field: str,
    policy: Policy,
    today: date,
    sent_topics: set[str],
) -> tuple[str, str]:
    """Return one bounded action without turning it into another intake question.

    The action never changes the profile, requirement or question ledgers.  Topic
    history only helps a later repeat choose a different useful task where one is
    available; if every task was already shared, the most relevant one is safely
    reinforced instead of inventing a new requirement.
    """

    zh = case.customer_language == "zh"
    include_source = not wants_no_links(case.latest_customer_message)
    candidates: list[tuple[str, str]] = []

    passport_kinds = {"passport", "travel_document"}
    if not _has_usable_document(case, passport_kinds):
        candidates.append((
            "pending_passport_preparation_v1",
            (
                "你可以先做一件不受待补信息影响的事：把护照资料页扫描成一份清晰、完整的 PDF，"
                "确认姓名、出生日期、护照号码和有效期都能看清，页面四边不要裁掉。准备好后直接回复这封邮件附上即可，我会按当前档案一起核对。"
                if zh else
                "In the meantime, you can complete one useful task: scan the passport details page as a clear, "
                "complete PDF. Make sure the name, date of birth, passport number and validity dates are legible "
                "and that none of the page edges are cropped. When ready, reply to this email with the attachment "
                "and I will check it against the case record."
            ) + _source(policy, "passport", today, include=include_source),
        ))

    if (
        any(item.id == "legal_residence" and item.applicable and not item.satisfied for item in case.requirements)
        and not _has_usable_document(case, {"residence_permit", "visa", "status_document"})
    ):
        candidates.append((
            "pending_residence_preparation_v1",
            (
                "你也可以先找出在递交地的居留身份文件，核对上面的姓名、身份类别和有效期，并保存一份清晰 PDF。"
                "这份材料用于说明你在递交地的合法居留身份。"
                if zh else
                "You can also locate the document showing your residence status where you will apply, check the "
                "name, status category and validity, and keep a clear PDF. This helps explain your lawful residence "
                "in the place of application."
            ) + _source(policy, "legal_residence", today, include=include_source),
        ))

    if case.profile.occupation_status == "student" and not _has_usable_document(case, {"student_letter"}):
        candidates.append((
            "pending_student_letter_check_v1",
            (
                "你还可以先向学校索取在读证明；拿到后核对姓名、课程、目前在读状态、出具日期和学校联系方式是否清楚。"
                if zh else
                "You can also request an enrolment letter from your school. When it arrives, check that your name, "
                "course, current enrolment, issue date and the school's contact details are clear."
            ) + _source(policy, "status_evidence", today, include=include_source),
        ))
    elif case.profile.occupation_status == "employed" and not _has_usable_document(case, {"employment_letter"}):
        candidates.append((
            "pending_employment_letter_check_v1",
            (
                "你还可以先向公司索取抬头纸在职证明；拿到后核对职位、薪资、入职时间、出具日期和公司联系方式是否与申请资料一致。"
                if zh else
                "You can also ask your employer for a letter on headed paper. Check that your role, salary, start "
                "date, the letter's issue date and employer contact details match the application information."
            ) + _source(policy, "status_evidence", today, include=include_source),
        ))
    elif case.profile.occupation_status == "self_employed" and not _has_usable_document(
        case, {"self_employment_evidence"}
    ):
        candidates.append((
            "pending_self_employment_check_v1",
            (
                "你还可以先整理现有的经营登记材料或近期业务发票，选出能清楚说明业务持续经营和收入来源的文件。"
                if zh else
                "You can also organise your business registration records or recent invoices and identify the items "
                "that most clearly show the business is operating and where its income comes from."
            ) + _source(policy, "status_evidence", today, include=include_source),
        ))

    if case.profile.funding_source == "self" and not _has_usable_document(case, {"bank_statement"}):
        candidates.append((
            "pending_self_funding_check_v1",
            (
                "资金部分可以先从网银下载正式对账单，核对账户姓名、可用资金和主要资金来源是否看得清；不要只保留余额截图。"
                if zh else
                "For the funding evidence, you can download official statements from online banking and check that "
                "the account holder, accessible funds and main sources of money are clear; keep more than a balance screenshot."
            ) + _source(policy, "funding_evidence", today, include=include_source),
        ))
    elif case.profile.funding_source == "personal_sponsor" and not _has_usable_document(
        case, {"sponsor_letter", "sponsor_funds", "relationship_evidence", "sponsor_uk_status"}
    ):
        candidates.append((
            "pending_personal_sponsor_check_v1",
            (
                "资助部分可以先请资助人准备一份简短说明，写清与你的关系、具体承担哪些费用和怎样支付；"
                "资金能力及关系证明要分开准备。"
                if zh else
                "For the sponsorship evidence, ask the sponsor for a short statement explaining their relationship "
                "to you, the exact costs they will cover and how they will pay. Keep evidence of their funds and of "
                "the relationship as separate supporting items."
            ) + _source(policy, "sponsor_evidence", today, include=include_source),
        ))

    if case.profile.visit_purpose == "tourism" and not _has_usable_document(case, {"itinerary_description"}):
        candidates.append((
            "pending_itinerary_check_v1",
            (
                "行程方面可以先做一页预计计划，列出打算去的城市、大致活动和住宿地区；没定的内容标注待确认，"
                "不用为了材料先买机票或订酒店。"
                if zh else
                "For the trip plan, you can draft one page listing the cities, broad activities and intended "
                "accommodation areas. Mark undecided details as provisional; there is no need to buy flights or "
                "book a hotel merely to create evidence."
            ) + _source(policy, "purpose_evidence", today, include=include_source),
        ))

    if not candidates:
        # A useful housekeeping task that is safe before any remaining profile
        # field is answered and does not claim another document is mandatory.
        candidates.append((
            "pending_file_housekeeping_v1",
            (
                "你可以先把已有文件按“文件类型－姓名－日期”命名，并保留清晰的原始 PDF；这样后续核对时不容易混淆版本。"
                if zh else
                "You can organise the files you already have using names such as document type, applicant name and "
                "date, while keeping the clear original PDFs. This reduces version mix-ups during the later review."
            ),
        ))

    preferred_topics = {
        "full_name": {"pending_passport_preparation_v1"},
        "date_of_birth": {"pending_passport_preparation_v1"},
        "nationality_country": {"pending_passport_preparation_v1"},
        "application_country": {"pending_residence_preparation_v1"},
        "occupation_status": {
            "pending_student_letter_check_v1",
            "pending_employment_letter_check_v1",
            "pending_self_employment_check_v1",
        },
        "funding_source": {
            "pending_self_funding_check_v1",
            "pending_personal_sponsor_check_v1",
        },
        "visit_purpose": {"pending_itinerary_check_v1"},
    }.get(pending_field, set())
    ordered = sorted(candidates, key=lambda item: item[0] not in preferred_topics)
    topic, action = next((item for item in ordered if item[0] not in sent_topics), ordered[0])
    return topic, f"{action}\n\n{pending_question_reminder(case)}"
