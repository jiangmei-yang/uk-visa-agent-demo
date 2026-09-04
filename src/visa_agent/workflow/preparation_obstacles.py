"""Small, reviewed next actions for two concrete preparation obstacles.

This selector neither validates substitute evidence nor changes a requirement,
profile value, date deferral, confirmation or permission. The outer next-step
selector retains all existing review and pause priorities.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Literal

from visa_agent.domain.models import Case, CaseStatus, DocumentStatus, GateResult, NextStepAdvice
from visa_agent.domain.policy import Policy
from visa_agent.workflow.advice_preferences import wants_no_links
from visa_agent.workflow.conversation import latest_reply_text
from visa_agent.workflow.document_purpose import APPLICATION_SOURCE, DOCUMENTS_SOURCE

REVIEWED_POLICY_VERSION = "2026-02-25"
SOURCE_CHECKED_AT = date(2026, 9, 4)
SOURCE_REVIEW_AFTER = date(2026, 10, 4)

_UNSAFE_SCOPE = (
    r"如果|假如|假设|除非|假定|\b(?:if|unless|assuming|suppose|hypothetical)\b|"
    r"(?:不要|不用|不想|不需要|别).{0,15}(?:告诉|解释|回答|继续|准备|建议)|"
    r"不是问|(?:并非|不是|没有说|没说).{0,12}(?:不给|拿不到|未定|没定|没确定|没有确定|尚未确定|定不下来)|"
    r"\b(?:not asking|do not suggest|don't suggest|do not tell|don't tell|do not answer|don't answer|not true|not the case)\b|"
    r"(?:模板|示例|例子)(?:里|上)?(?:写着|说|写道)|"
    r"\b(?:template|example)\b.{0,25}\b(?:says?|said|reads?|wrote)\b|"
    r"(?:以后|将来|下次).{0,8}(?:会|再)?问|\bI\s+(?:will|may|might)\s+ask\b|"
    r"\b(?:friend|sister|brother|client|customer|applicant)\b.{0,20}\b(?:asks?|asked|said|wrote|needs?|employer|dates)\b|"
    r"\b(?:his|her|their)\s+(?:employer|employment letter|travel dates|application)\b|"
    r"\bon behalf of\b|(?:朋友|同学|客户|姐姐|妹妹|弟弟|哥哥|他|她)(?:说|问|的?.{0,8}(?:公司|日期|申请))|"
    r"(?:代|替|帮)(?:我的?|一位)?(?:朋友|同学|客户|他|她).{0,12}(?:问|准备|申请)|"
    r"保证|包过|获批|过签|够不够|足够|造假|伪造|编造|改.{0,5}(?:工资|薪资|收入)|"
    r"\b(?:guarantee\w*|approv\w*|enough|sufficient|fake|forg\w*|fabricat\w*|invent\w*)\b|"
    r"学生签证|留学签证|工作签证|工签|结婚签证|配偶签证|过境|医疗|移民|"
    r"(?:加拿大|美国|澳洲|澳大利亚|申根).{0,8}签证|"
    r"\b(?:student|work|marriage|spouse|transit|Canadian|American|Australian|Schengen) visa\b|"
    r"\b(?:skilled worker|graduate visa|medical treatment|paid engagement|permanent residen\w*)\b"
)
_NEXT_ACTION = (
    r"(?:下一步|接下来|现在).{0,14}(?:准备|整理|做).{0,8}(?:什么|哪些)|"
    r"(?:可以|能|该|应该)先(?:准备|整理|做)(?:什么|哪些)|"
    r"\bwhat\s+(?:else\s+)?(?:can|should|could|do)\s+I\s+(?:prepare|organise|organize|do|work on)"
    r"(?:\s+\w+){0,6}\s+(?:now|next|first)\b|"
    r"\bwhat\s+(?:else\s+)?(?:can|should|could)\s+I\s+(?:prepare|organise|organize)\b"
)
# Date-specific alternatives: an ordinary request to start now need not repeat
# the word "prepare". Keep the existing employment-letter trigger unchanged.
_DATE_NEXT_ACTION = (
    r"有(?:什么|哪些)(?:事|事情)?(?:我)?(?:现在)?(?:可以|能)先(?:做|准备|整理)(?:的)?|"
    r"有没有(?:什么|哪些)?(?:现在)?(?:可以|能)(?:先)?(?:做|准备|整理)的(?:事|事情)|"
    r"(?:我)?(?:现在)?(?:可以|能|该|应该)(?:先)?从哪里开始|"
    r"\b(?:is there anything|are there any tasks?)\s+(?:that\s+)?I\s+(?:can|could)\s+"
    r"(?:do|prepare|organise|organize|work on)(?:\s+(?:now|first|for now|in the meantime))?\s*(?:[?？.!。]|$)|"
    r"\bcan I\s+(?:get started|make a start)(?:\s+on anything)?\s+(?:now|in the meantime)\b|"
    r"\bwhere\s+(?:can|could|should)\s+I\s+(?:start|begin)(?:\s+(?:now|for now))?\s*(?:[?？.!。]|$)"
)
_LETTER_UNAVAILABLE = (
    r"(?:公司|雇主|人事).{0,18}(?:不给|不肯|拒绝|暂时不能|暂时无法).{0,8}(?:开|出具|提供).{0,8}(?:在职证明|雇主信)|"
    r"(?:我|现在|暂时).{0,8}(?:拿不到|开不出|无法拿到).{0,8}(?:在职证明|雇主信)|"
    r"\bmy\s+employer\s+(?:will not|won't|cannot|can't|refuses? to|is unable to)\s+"
    r"(?:issue|provide|write)\b.{0,25}\b(?:employment|employer) letter\b|"
    r"\bI\s+(?:cannot|can't|am unable to)\s+(?:get|obtain)\b.{0,15}\b(?:employment|employer) letter\b"
)
_DATES_UNDECIDED = (
    r"(?:日期|行程|时间).{0,12}(?:定不下来|没定|未定|没确定|没有确定|还没确定|尚未确定)|"
    r"\b(?:my\s+)?(?:travel\s+)?dates\s+(?:(?:are|remain)\s+)?(?:still\s+)?"
    r"(?:undecided|unknown|unconfirmed|not (?:set|decided|confirmed))\b|"
    r"\bI\s+(?:still\s+)?(?:haven't|have not|cannot|can't)\s+(?:yet\s+)?"
    r"(?:decided|decide|set|confirm)\b.{0,20}\b(?:travel\s+)?dates\b"
)


def preparation_obstacle_kind(text: str) -> Literal["employment_letter", "dates"] | None:
    if not text or len(text) > 6000:
        return None
    current = latest_reply_text(text)
    if re.search(_UNSAFE_SCOPE, current, re.I):
        return None
    current = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|`[^`]*`', "", current)
    current = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)", "", current)
    explicit_preparation = bool(re.search(_NEXT_ACTION, current, re.I))
    if explicit_preparation and re.search(_LETTER_UNAVAILABLE, current, re.I):
        return "employment_letter"
    if (re.search(_DATES_UNDECIDED, current, re.I)
            and (explicit_preparation or re.search(_DATE_NEXT_ACTION, current, re.I))):
        return "dates"
    return None


def _review(zh: bool, *, source: bool = False) -> NextStepAdvice:
    if source:
        message = ("这项准备建议需要先复核当前官方依据，再安排材料。" if zh else
                   "The current official basis for this preparation advice needs checking before choosing material.")
    else:
        message = ("先核对适用的入境路线，再按你的实际情况安排材料；不能直接套用普通访客清单。" if zh else
                   "First check the applicable entry route before choosing material for your circumstances; "
                   "the ordinary visitor checklist cannot be assumed to apply.")
    return NextStepAdvice(message=message, kind="review")


def reviewed_obstacle_next_step(case: Case, policy: Policy, gate: GateResult) -> NextStepAdvice | None:
    obstacle = preparation_obstacle_kind(case.latest_customer_message)
    if obstacle is None or case.preparation_paused or case.status != CaseStatus.DRAFT:
        return None
    if (case.open_blockers() or case.human_review_reason or case.profile.has_serious_history is True
            or gate.checks.get("all_held_updates_reviewed") is False
            or any(doc.status in {DocumentStatus.HUMAN_REVIEW_REQUIRED, DocumentStatus.NEEDS_REPLACEMENT,
                                  DocumentStatus.NEEDS_CLARIFICATION} for doc in case.documents)):
        return None  # Existing review/replacement rendering retains its precise reason.
    zh = case.customer_language == "zh"
    if (gate.checks.get("policy_snapshot_is_current") is not True
            or policy.version != REVIEWED_POLICY_VERSION or case.policy_version != policy.version
            or policy.valid_until > SOURCE_REVIEW_AFTER
            or not {DOCUMENTS_SOURCE, APPLICATION_SOURCE} <= set(policy.sources)):
        return _review(zh, source=True)
    profile = case.profile
    # The persisted profile's False is also the historical default. Only a
    # sourced negative is an explicit rejection; model_fields_set cannot make
    # that distinction after a complete JSON snapshot has been reloaded.
    explicit_other_route = any(
        evidence.fact_key == "route_confirmed_standard_visitor" and evidence.value is False
        and not evidence.superseded for evidence in case.evidence
    )
    if explicit_other_route or any(value is not None and value not in policy.scope[key]
                                  for key, value in (
        ("purposes", profile.visit_purpose), ("occupations", profile.occupation_status),
        ("funding", profile.funding_source),
    )):
        return _review(zh)
    if (not profile.nationality_country or not profile.application_country
            or profile.visit_purpose is None or profile.occupation_status is None
            or profile.funding_source is None):
        # Ordinary missing background is not evidence of an unsuitable route.
        # The normal next-step selector can ask for the genuinely needed fact.
        return None
    if obstacle == "employment_letter" and profile.occupation_status != "employed":
        return None
    if obstacle == "dates" and profile.planned_arrival_date and profile.planned_departure_date:
        return None  # Do not hide a conflict with recorded dates or erase them here.

    rules = {rule.id: rule for rule in policy.requirements}
    items = {item.id: item for item in case.requirements}
    for item in case.requirements:
        if not item.applicable or item.satisfied:
            continue
        rule = rules.get(item.id)
        if (rule is None or item.rule_version != policy.version or rule.version != policy.version
                or rule.source_url not in {DOCUMENTS_SOURCE, APPLICATION_SOURCE}):
            return _review(zh, source=True)

    occupation = profile.occupation_status
    funding_source = profile.funding_source
    assert occupation is not None and funding_source is not None
    status = {
        "employed": (("employment_letter",),
            "可以先向公司核对在职证明的办理方式，整理职位、薪资和任职时间，供雇主用公司抬头纸确认；现在不用把请假写成已批准。",
            "You can ask your employer how to obtain an employment letter and organise your role, salary and length "
            "of employment for confirmation on company-headed paper; do not present leave as already confirmed."),
        "student": (("student_letter",),
            "可以先向学校索取确认当前在读情况的抬头纸证明；先核对姓名和在读状态，请假安排等日期确定后再如实补充。",
            "You can ask your school for a headed letter confirming current enrolment. Check your name and status "
            "now, and add accurate leave arrangements once the dates are known."),
        "self_employed": (("self_employment_evidence",),
            "可以先整理已有的经营登记或近期业务发票，把现有业务和收入来源对应起来，不需要为了准备而编造雇佣关系。",
            "You can organise existing business registration records or recent invoices, connecting your current "
            "business and income source without inventing an employment relationship."),
    }[occupation]
    funding = {
        "self": (("bank_statement",),
            "可以先从网银或银行索取正式银行对账单，核对可用资金及其来源，再和目前估计的旅行支出放在一起检查。",
            "You can obtain official bank statements through online banking or your bank, check accessible funds "
            "and their source, and compare them with the trip costs you currently estimate."),
        "employer_or_school": (("funding_letter", "sponsor_funds"),
            "可以先和资助单位或学校核对承担哪些费用、怎样支付，并整理能说明这项资助安排和资助能力的现有材料。",
            "You can check with the organisation or school funding you which costs it covers and how payment works, "
            "and organise existing evidence of that support and its ability to provide it."),
        "personal_sponsor": (("sponsor_letter", "sponsor_funds"),
            "可以先和资助人核对承担哪些费用、怎样提供支持以及与你的关系，并整理能说明可提供这项资助的现有材料。",
            "You can check with your sponsor which costs they cover, how support is provided and their relationship "
            "to you, then organise existing evidence that they can provide it."),
    }[funding_source]
    choices = [("funding_evidence", funding)]
    if obstacle == "dates":
        choices.insert(0, ("status_evidence", status))
    choices.extend([
        ("passport", (("passport", "travel_document"),
            "可以先把现有护照资料页整理成清晰、完整的副本，核对文字和页边没有被裁掉。",
            "You can organise a clear, complete copy of your existing passport details page, checking that its text "
            "and edges have not been cropped.")),
        ("legal_residence", (("status_document", "residence_permit", "visa"),
            "可以先整理你在申请地合法居留的现有证明，核对姓名和当前居留信息。",
            "You can organise existing evidence of lawful residence where you are applying, checking your name "
            "and current residence information.")),
    ])
    active = [doc for doc in case.documents if doc.status not in {
        DocumentStatus.SUPERSEDED, DocumentStatus.REQUESTED,
    }]
    selected = None
    for identifier, option in choices:
        candidate_item = items.get(identifier)
        if (candidate_item is not None and candidate_item.applicable and not candidate_item.satisfied
                and not any(doc.kind in option[0] for doc in active)):
            selected = identifier, option[1] if zh else option[2]
            break

    route_note = ""
    if profile.route_confirmed_standard_visitor is not True:
        route_note = ("适用路线还需确认；如果按普通访客路线准备，" if zh else
                      "The applicable route still needs confirming; if preparing under the ordinary visitor route, ")
    if obstacle == "employment_letter":
        intro = ("公司目前不开在职证明，这个困难需要单独核实，不必为此停下所有准备。" if zh else
                 "The difficulty obtaining your employer letter needs a separate check; it does not prevent all other preparation.")
        limitation = ("这些材料不能自动替代在职证明；取得困难和现有证据需要顾问另行核对，在职情况这项检查仍保留。" if zh else
                      "These records do not automatically replace the employer letter. An adviser still needs to check "
                      "the difficulty and available evidence; this does not waive the employment-evidence check.")
    else:
        intro = ("日期还没定，可以先做不依赖最终日期的准备。" if zh else
                 "With dates still undecided, you can start with preparation that does not depend on final dates.")
        limitation = ("在线申请仍需要计划旅行日期，护照是否覆盖整个停留也要等日期确定后核对；现在不猜日期，也不把准备进展当作可以定稿。" if zh else
                      "The online application still needs planned travel dates, and passport validity for the whole stay "
                      "must be checked when dates are confirmed. Do not invent dates or treat this progress as readiness to finalise.")
    if selected is None:
        if obstacle == "employment_letter":
            action = ("可以先记下向公司申请证明的时间、公司的实际答复，以及已提供材料中哪些能说明任职情况，供顾问核对这个困难；不要编造公司答复，也不用重发已收到的文件。" if zh else
                      "You can note when you requested the letter, the employer's actual response, and which records "
                      "already provided describe your employment, so an adviser can check this difficulty. "
                      "Do not invent a response or resend files already received.")
        else:
            action = ("可以先列一份不带具体日期的旅行目的和拟访问地点草稿，把尚未确定的安排留作待定；这能帮助之后核对行程，不是最终行程，也不用重发已收到的文件。" if zh else
                      "You can draft the purpose of the trip and intended places to visit without assigning dates, "
                      "leaving undecided arrangements open for later checking. This is not a final itinerary, "
                      "and files already received need not be resent.")
    else:
        action = selected[1]
        if active:
            action += ("已经收到的文件不用重发。" if zh else " Files already received do not need to be resent.")
    message = intro + "\n" + route_note + action + "\n" + limitation
    if not wants_no_links(case.latest_customer_message):
        message += "\nGOV.UK: " + DOCUMENTS_SOURCE + "#demonstrating-personal-circumstances"
        if obstacle == "dates":
            message += "\nGOV.UK: " + APPLICATION_SOURCE + "#documents-and-information-youll-need-to-apply"
    return NextStepAdvice(message=message, kind="document" if selected else "waiting",
                          requirement_id=selected[0] if selected else None)
