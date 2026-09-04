"""Small, source-reviewed preparation steps selected from the current case, not legal decisions."""

import re
from datetime import date

from visa_agent.domain.models import Case, CaseStatus
from visa_agent.workflow.advice_preferences import _current_clauses, wants_no_links
from visa_agent.workflow.conversation import (
    customer_requests_next_step,
    document_list_requested,
    latest_reply_text,
    preparation_context_progress,
    quiet_preparation_resume,
)
from visa_agent.workflow.customer_questions import (
    _active_clauses,
    _next_step_targets_current_case,
    is_generic_uk_preparation_enquiry,
)

APPLICATION_URL = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"
ROUTE_CHECK_URL = "https://www.gov.uk/check-uk-visa"
DOCUMENTS_URL = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
CHECKED_AT = date(2026, 9, 4)
REVIEW_AFTER = date(2026, 10, 4)


def _information_only_request(active: str) -> bool:
    return bool(re.search(
        r"(?:只|仅).{0,12}(?:问|说|告诉|确认|核对|列出).{0,16}(?:信息|个人资料|姓名|生日|出生|住址)|"
        r"(?:只|仅).{0,8}(?:需要|想知道).{0,12}(?:信息|个人资料)|"
        r"(?:哪|什么|哪些).{0,8}(?:个人)?信息|"
        r"(?:只|仅).{0,6}(?:问|提).{0,6}(?:一个|一项|一条|1个).{0,6}(?:问题|信息)|"
        r"\b(?:just|only) (?:tell|ask|say|list).{0,35}(?:information|details?|name|birthday|date of birth)|"
        r"\b(?:only|just) (?:need|want).{0,15}(?:information|details?)|"
        r"\b(?:what|which) (?:information|personal details)\b|"
        r"\b(?:just|only).{0,8}(?:ask|give).{0,12}(?:one|a single) question\b", active, re.I,
    ))


def _initial_material_enquiry(body: str) -> bool:
    """Current general UK-application enquiry, not a request for missing form fields."""
    # Share the policy classifier's whole-message boundary. Removing a condition,
    # quote or third-party clause first could turn a non-request into orientation.
    return not _information_only_request(body) and is_generic_uk_preparation_enquiry(body)


def _incomplete_personal_checklist(case: Case, body: str) -> bool:
    """A validated personal checklist may accompany new grounded profile details.

    This is not the generic-unsupported rescue: the model must already have supplied
    only a checklist intent, with current context progress and an independent own-case
    question. It offers conditional guidance while a material driver is still missing.
    """
    if (set(case.customer_question_topics) != {"document_checklist"}
            or case.customer_answers or document_list_requested(case)
            or case.profile.visit_purpose not in {"tourism", "family_or_friends", "business", "conference"}
            or case.profile.occupation_status not in {"student", "employed", "self_employed"}
            or not {"visit_purpose", "occupation_status", "funding_source", "nationality_country", "application_country"}
            .intersection(set(case.latest_received_facts) | set(case.latest_changes))
            or re.search(r"如果|假如|假设|除非|保证|获批|过签|批准|"
                         r"\b(?:if|unless|assuming|suppose|guarantee|approval|approved|eligible)\b", body, re.I)):
        return False
    for clause in _active_clauses(body, split_commas=False):
        if (re.fullmatch(
                r"(?:请问)?(?:我这次申请|我的申请|我)(?:还)?(?:需要|准备|提供)(?:哪些|什么)(?:材料|资料|文件)[？?。.!！\s]*|"
                r"(?:which|what) (?:supporting )?documents (?:do I (?:still )?need|should I prepare) "
                r"for my (?:visitor |visa )?application[?.!\s]*", clause, re.I)
                and _next_step_targets_current_case(body, clause)):
            return True
    return False


def _question_step_allows_preparation_guidance(case: Case, active: str, *, initial_enquiry: bool = False) -> bool:
    """A request to start preparing can be labelled next_step without being a FAQ.

    Only replace the planner's generic missing-detail introduction, never its actual
    answer about a document/review/paused state or another customer question.
    """
    step = case.next_step_advice
    if (step is None or step.kind != "question"
            or set(case.customer_question_topics) != {"next_step"}
            or case.customer_answers != [step.message]):
        return False
    if _information_only_request(active):
        return False
    if initial_enquiry:
        return True
    if (case.profile.visit_purpose not in {"tourism", "family_or_friends", "business", "conference"}
            or not (case.profile.occupation_status in {"student", "employed", "self_employed"}
                    or case.profile.funding_source in {"self", "personal_sponsor", "employer_or_school"})):
        return False
    for clause in _active_clauses(active, split_commas=False):
        if re.search(
            r"^(?:如果|假如|假设|if\b|suppose\b|maybe\b)|"
            r"(?:不想|不用|不要|先不|暂不|不能|无需).{0,8}(?:准备|申请|整理)|"
            r"\b(?:don't|do not|not asking|cannot).{0,15}(?:prepar\w*|organis\w*|organiz\w*|apply)",
            clause, re.I,
        ):
            continue
        if re.search(
            r"(?:帮我|请|想|先|开始|继续|接着|打算).{0,10}(?:准备|整理|收集).{0,10}(?:申请|材料|资料|文件|签证)|"
            r"\b(?:help me|please|let['’]s|can we|could we|want to|ready to|start|continue)"
            r".{0,24}(?:prepar\w*|collect\w*|organis\w*|organiz\w*).{0,24}(?:documents?|application|evidence)",
            clause, re.I,
        ):
            return True
    return False


def preparation_guidance(case: Case, today: date, sent_topics: set[str]) -> list[tuple[str, str]]:
    """A link preference changes presentation, not whether useful guidance exists."""
    result = _preparation_guidance(case, today, sent_topics)
    if wants_no_links(case.latest_customer_message):
        return [(topic, "\n".join(line for line in answer.splitlines()
                                 if not line.startswith("GOV.UK: ")))
                for topic, answer in result]
    return result


def _preparation_guidance(case: Case, today: date, sent_topics: set[str]) -> list[tuple[str, str]]:
    """Offer at most two useful next steps; explicit answers and problems take priority.

    Topic IDs are versioned. Only topics in actually sent replies count as already shared.
    These suggestions cannot change requirements, evidence acceptance, facts or consent.
    """
    current = latest_reply_text(case.latest_customer_message)
    no_links = wants_no_links(current)
    active = "\n".join(_active_clauses(current, split_commas=False))
    initial_enquiry = _initial_material_enquiry(current)
    personal_checklist = _incomplete_personal_checklist(case, current)
    question_preparation = _question_step_allows_preparation_guidance(case, active, initial_enquiry=initial_enquiry)
    initial_checklist = ((set(case.customer_question_topics) <= {"document_checklist"} or question_preparation)
                         and not document_list_requested(case) and initial_enquiry)
    if (case.preparation_paused or quiet_preparation_resume(case)
            or not CHECKED_AT <= today <= REVIEW_AFTER or (case.customer_answers and not question_preparation)
            or (case.customer_question_topics and not initial_checklist and not question_preparation and not personal_checklist)
            or case.open_blockers()
            or case.latest_document_names or case.status != CaseStatus.DRAFT):
        return []
    # Preserve comma-linked conditions and negations before selecting an affirmative
    # preparation request. A separate later-date statement does not veto that request.
    preparation_clauses = (_active_clauses("\n".join(_current_clauses(current))) if no_links
                           else _active_clauses(current, split_commas=False))
    text = "\n".join(clause for clause in preparation_clauses
                     if not re.search(
                         r"如果|假如|暂时|先不|不想|不需要|不能|尚未|还没|"
                         r"\b(?:if|not|later|tomorrow|maybe|cannot|never|stop)\b|"
                         r"(?:don|can|won|wouldn|couldn|shouldn)['’]t", clause, re.I,
                     ))
    # An unshared topic is not by itself a reason to send it now. Proactive advice
    # needs progress in intake or a current preparation request; existing profile
    # data must not turn unrelated chatter or control instructions into a brochure.
    if not (question_preparation or preparation_context_progress(case) or customer_requests_next_step(current)
            or re.search(
                r"(?:想|准备|打算|需要).{0,6}(?:申请|办理?).{0,6}(?:英国|签证)|"
                r"(?:准备|整理|收集|补充).{0,8}(?:材料|资料|文件)|"
                r"(?:材料|资料|文件).{0,8}(?:准备|整理|收集)|"
                r"\b(?:prepar\w*|collect\w*|gather\w*|organis\w*|organiz\w*).{0,24}"
                r"(?:documents?|evidence|application)|"
                r"\b(?:want|need|planning) to apply.{0,20}(?:UK|visa)\b",
                text, re.I,
            ) or (initial_checklist and re.search(
                r"(?:英国|UK|British).{0,24}(?:签证|旅游|旅行|visa|visit|trip)|"
                r"(?:visa|visit|trip).{0,16}(?:UK|Britain)", text, re.I,
            ))):
        return []
    if (any(re.search(r"(?:不用|不需要|不要|无需|不想)[^，,;；。\n]{0,18}(?:流程|材料|建议|说明)|"
                      r"(?:don't|do not|no need|stop)[^,;\n]{0,30}(?:guidance|explain|advice)", clause, re.I)
            for clause in _current_clauses(current))
            or re.fullmatch(r"(?:谢谢|好的|收到|了解|thanks|thank you|okay|ok)[。.!！\s]*", text, re.I)):
        return []
    profile = case.profile
    zh = case.customer_language == "zh"
    result: list[tuple[str, str]] = []
    if initial_checklist and "route_orientation_v1" not in sent_topics:
        result.append(("route_orientation_v1", (
            "可以先按这几个方向整理：护照或旅行证件、赴英目的、旅行费用由谁承担，以及目前的工作或学习情况。"
            "具体需要哪些证明，要看你的访问和资助安排；现在不用一次上传所有材料。"
            "是否需要签证或 ETA，先结合护照和访问目的用官方入口查一下。"
            if zh else "Start by gathering what explains your passport or travel document, the purpose of the visit, "
            "how the trip will be paid for, and your work or studies. The supporting documents depend on "
            "your visit and funding arrangements; you do not need to upload everything now. "
            "Use the official checker to establish whether your passport and visit require a visa or ETA."
        ) + "\nGOV.UK: " + ROUTE_CHECK_URL + (("\n如果需要 Standard Visitor 签证，可以在 GOV.UK 在线填写申请，表格可以保存后继续。"
            if zh else "\nIf you need a Standard Visitor visa, apply online through GOV.UK; you can save the form and return to it.")
            if no_links else ("\n如果需要 Standard Visitor 签证，下面是在线申请入口，可以保存后再继续填写。"
            if zh else "\nIf you need a Standard Visitor visa, this is the online application page; you can save the form and return to it."))
            + "\nGOV.UK: " + APPLICATION_URL))
    if not initial_checklist and profile.visit_purpose not in {
        "tourism", "family_or_friends", "business", "conference"
    }:
        if profile.visit_purpose is None and "route_orientation_v1" not in sent_topics:
            return [("route_orientation_v1", (
                "先确认适合的申请类别，再准备材料。是否需要签证或 ETA，要结合护照和赴英目的判断；"
                "可以先在 GOV.UK 的 Check if you need a UK visa 页面查看，不用现在就把所有个人资料发来。"
                if zh else "Let's establish the right application route before collecting documents. "
                "Whether you need a visa or ETA depends on your passport and purpose; the official "
                "checker on GOV.UK is a starting point. We can then work out your preparation steps "
                "without asking you to send every personal detail at once."
            ) + "\nGOV.UK: " + ROUTE_CHECK_URL)]
        return []
    if not initial_checklist and "application_overview_v1" not in sent_topics:
        result.append(("application_overview_v1", (
            "申请从 GOV.UK 的 Apply now 开始在线填表，未填完的表格可以保存。"
            "如果需要 Standard Visitor 签证，流程是在线申请、预约签证申请中心，再按要求完成身份核验和交材料。"
            "我们这里帮你梳理信息、核对材料并整理材料包；正式递交由你在官网完成。"
            if zh else "Start by choosing Apply now on GOV.UK. "
            "You can save an unfinished form. If you need a Standard Visitor visa, apply online, "
            "book a visa application centre appointment, and follow the identity and document steps. "
            "We help organise and check your preparation pack; you submit the application on the official site."
        ) + "\nGOV.UK: " + APPLICATION_URL))
    # Existing combined student advice covers both components. Do not re-send
    # either component merely because a deployment now has more granular topics.
    covered = set(sent_topics)
    if "student_self_preparation_v1" in covered:
        covered.update({"student_enrolment_preparation_v1", "self_funding_preparation_v1"})
    if "family_personal_sponsor_preparation_v1" in covered:
        covered.update({"family_visit_preparation_v1", "personal_sponsor_preparation_v1"})
    candidates: list[tuple[str, str]] = []
    if profile.funding_source == "personal_sponsor" and "personal_sponsor_preparation_v1" not in covered:
        family = profile.visit_purpose == "family_or_friends"
        sponsor_text = (
            "先请资助人说明愿意承担哪些费用、怎样支付，再准备能说明你们关系和对方资金情况的材料。"
            "这样可以把“谁来付、付哪些、是否承担得起”对应起来；也需要看对方自身及家人的生活开支。"
            if zh else "Ask your sponsor to explain which costs they will cover and how they will pay, "
            "then gather evidence of your relationship and their available funds. This connects the promise "
            "of support to how it will work, including their own and their dependants' living costs."
        )
        if profile.sponsor_is_in_uk is True:
            sponsor_text += ("资助人在英国，还要准备其合法身份或居留证明。" if zh else
                             " As your sponsor is in the UK, include evidence of their lawful status there.")
        elif profile.sponsor_is_in_uk is None:
            sponsor_text += ("如果资助人在英国，再补其合法身份或居留证明。" if zh else
                             " If the sponsor is in the UK, include evidence of their lawful status there.")
        if family:
            sponsor_text = ("这次探亲访友，可以先和亲友核对访问和住宿安排；接待你的人不一定就是资助人。"
                            if zh else "For your visit to family or friends, agree the visit and accommodation plans "
                            "with them first; your host is not necessarily your sponsor. ") + sponsor_text
        candidates.append(("family_personal_sponsor_preparation_v1" if family else "personal_sponsor_preparation_v1",
                           sponsor_text + "\nGOV.UK: " + DOCUMENTS_URL + "#if-you-have-a-sponsor"))
    if profile.visit_purpose == "family_or_friends" and "family_visit_preparation_v1" not in covered:
        family_text = (
            "探亲访友可以先和对方核对你们的关系、访问安排，以及准备住在哪里。"
            "也可以请对方用一封简短的邀请说明把这些安排写清楚，帮助解释访问目的。"
            "对方是否提供住宿或承担费用要单独确认，不能仅凭是亲友就当作资助人。"
            if zh else "For a visit to family or friends, start by checking your relationship, visit plans "
            "and where you expect to stay with them. A short invitation explaining those arrangements "
            "can help set out the purpose of the trip. "
            "Check separately whether they will provide accommodation or pay any costs; being your host "
            "does not by itself mean they are funding you."
        )
        if profile.funding_source == "self":
            family_text = (
                "这次是探亲访友，可以请亲友写一封简短的邀请说明，把你们的关系、访问目的和住宿安排交代清楚。"
                "费用按你说的由自己承担，邀请说明里不用把接待写成经济资助。"
                if zh else "For this visit to family or friends, you can start by asking for a short invitation "
                "explaining your relationship, the purpose of the visit and the accommodation plans. "
                "You have said you will pay for the trip, so the invitation should not describe your host as funding it."
            )
            if not profile.uk_accommodation:
                family_text += ("住宿安排可以再和对方核对。" if zh else
                                " You can agree the accommodation arrangements with them next.")
        candidates.append(("family_visit_preparation_v1", family_text
                           + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    if (profile.occupation_status == "student" and profile.funding_source == "self"
            and not {"student_enrolment_preparation_v1", "self_funding_preparation_v1"} & covered):
        candidates.append(("student_self_preparation_v1", (
            "材料方面，可以先准备学校的在读证明，以及能说明资金来源和可用资金的银行流水。"
            "前者帮助说明学习情况，后者用于核对旅行费用如何承担；预算数字本身不能代替资金证明。"
            if zh else "As a self-funded student, you can start with a letter confirming your enrolment "
            "and bank statements showing accessible funds and their source. These help explain your "
            "circumstances and how you will pay for the trip; a budget figure is not funding evidence."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "student" and "student_enrolment_preparation_v1" not in covered:
        enrolment_text = (
            "你还在读书，可以先向学校索取在读证明，用来说明目前的学习情况。"
            "如果行程涉及请假，也可以请学校说明相应安排。"
            if zh else "As you are studying, you can ask your school for a letter confirming your enrolment. "
            "It helps explain your current circumstances; if the trip involves leave from your course, "
            "ask the school to explain that arrangement too."
        )
        if profile.funding_source is None:
            enrolment_text += ("即使费用由谁承担还没决定，也不妨碍先准备这一部分。" if zh else
                               " You can start this part even before deciding who will pay.")
        candidates.append(("student_enrolment_preparation_v1", enrolment_text
                           + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "employed" and "employment_preparation_v1" not in covered:
        candidates.append(("employment_preparation_v1", (
            "你现在在职，可以先向公司人事索取一封用公司抬头纸出具的在职证明，写明职位、薪资和入职时间。"
            "它能说明目前的工作和收入情况；准备时顺便核对这些信息是否与申请表一致。"
            if zh else "You can start by asking HR for a letter on company headed paper with your role, "
            "salary and how long you have worked there. It helps explain your employment and income; "
            "check that these details match what you put in the application."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    elif profile.occupation_status == "self_employed" and "self_employment_preparation_v1" not in covered:
        candidates.append(("self_employment_preparation_v1", (
            "你自己经营业务，可以先找现有的经营登记材料或近期业务发票，说明业务确实在持续开展。"
            "不必把自己套进普通雇员的在职证明格式；我们要把你从事什么业务、收入从哪里来说明白。"
            if zh else "For your own business, start with business registration documents or recent invoices "
            "that show it is still operating. You do not need to force your circumstances into an employee-letter "
            "format; the aim is to explain what you do and where your income comes from."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    if profile.visit_purpose == "conference" and "conference_preparation_v1" not in covered:
        candidates.append(("conference_preparation_v1", (
            "这次是参会，可以先向主办方索取邀请函。它用于说明你要参加的活动和访问目的；"
            "谁承担费用的证明还需要另外结合你的资助安排核对。"
            if zh else "For the conference, start by asking the organiser for an invitation letter. "
            "It helps explain the event and purpose of your visit; funding evidence still needs "
            "to match whoever is covering the costs."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#attendees-of-business-related-events-or-conferences"))
    if profile.funding_source == "self" and "self_funding_preparation_v1" not in covered:
        candidates.append(("self_funding_preparation_v1", (
            "费用由你自己承担，可以先整理显示可用资金及其来源的银行流水，和预计旅行支出放在一起核对。"
            "重点是说明这趟旅行怎样负担，而不是只报一个预算数字。"
            if zh else "As you are paying for the trip, gather bank statements showing accessible funds "
            "and where they came from, then compare them with the costs you expect. The aim is to explain "
            "how you will afford the visit, not just to give a budget figure."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#demonstrating-personal-circumstances"))
    if profile.funding_source == "employer_or_school" and "organisation_funding_preparation_v1" not in covered:
        candidates.append(("organisation_funding_preparation_v1", (
            "既然由单位或学校资助，可以先请负责部门出具说明：资助哪些费用、怎样支付，以及与你的关系。"
            "例如直接支付和事后报销就要写清楚；还需要能说明资助方有能力承担这些费用的材料，便于核对整个资金安排。"
            if zh else "Ask the department funding you to explain which costs it covers, how payment works "
            "and its relationship to you. For example, clarify whether it pays directly or reimburses you. "
            "Evidence that it can provide that support helps make the funding arrangement clear."
        ) + "\nGOV.UK: " + DOCUMENTS_URL + "#if-you-have-a-sponsor"))
    # Funding just supplied or changed should not be buried behind an unrelated
    # occupational overview. Personal support/family context already lead above.
    changed_fields = set(case.latest_received_facts) | set(case.latest_changes)
    location_changed = bool({"nationality_country", "application_country"} & changed_fields)
    if (location_changed and "residence_preparation_v1" not in covered
            and any(item.id == "legal_residence" and item.applicable and not item.satisfied
                    for item in case.requirements)):
        residence = (
            "还有一项和申请地点有关：你可以先找出当地的居留文件，核对上面的姓名、身份类别和有效期。"
            "我们要用它说明你在申请地的合法居留身份；具体放哪份进材料包，要看你实际持有的文件。"
            if zh else "Given your passport country and where you will apply, check your evidence of lawful residence there. "
            "Find the document recording your current residence status and check its name, status and validity. "
            "It explains your residence where you apply; the location of your school or employer alone does not establish it. "
            "The appropriate evidence depends on the document you hold."
        )
        candidates.append(("residence_preparation_v1", residence + "\nGOV.UK: " + DOCUMENTS_URL
                           + "#demonstrating-personal-circumstances"))
    funding_changed = "funding_source" in changed_fields
    if "visit_purpose" in changed_fields and profile.visit_purpose == "conference":
        candidates.sort(key=lambda item: item[0] != "conference_preparation_v1")
    elif funding_changed and profile.funding_source == "employer_or_school":
        candidates.sort(key=lambda item: item[0] != "organisation_funding_preparation_v1")
    elif location_changed and not {"visit_purpose", "occupation_status", "funding_source"} & changed_fields:
        candidates.sort(key=lambda item: item[0] != "residence_preparation_v1")
    if candidates:
        result.append(candidates[0])
    return result[:2]
