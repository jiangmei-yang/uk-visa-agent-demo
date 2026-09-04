"""Reviewed explanations for explicit questions about an individual document.

This is not a sufficiency verdict, a mandatory checklist, or authority to collect a
document. Callers retain route, source freshness, quotation and question-scope checks.
"""

import re

DOCUMENTS_SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
APPLICATION_SOURCE = "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa"


def _purpose_kind(body: str) -> str | None:
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", body)
    # Questions about acceptance or false evidence are not answered by a generic
    # purpose explanation, even when they mention a supported document.
    if re.search(
        r"够不够|足够|保证|一定|能过|会不会.{0,8}(?:接受|获批)|编造|伪造|造假|"
        r"\b(?:sufficient|enough|guarantee|approved|accepted|invent|fake|fabricat\w*)\b|"
        r"学生签证|工作签证|结婚签证|\b(?:student|work|marriage|transit) visa\b", text, re.I,
    ):
        return None
    purpose = (
        r"作用|用途|为了|为什么|用来|旨在|解释|(?:说明|证明)什么|"
        r"\b(?:purpose|why|demonstrate|explain)\b|\bintended to (?:show|explain|demonstrate)\b|"
        r"\bwhat\b.{0,120}\b(?:show|explain|demonstrate)\b"
    )
    kinds: set[str] = set()
    for clause in re.split(r"[。！？!?；;\n]|\.(?:\s|$)", text):
        if (clause.lstrip().startswith(">")
                or re.search(r"(?:不用|不需要|不要|别|不想).{0,12}(?:解释|说|问)|"
                             r"\b(?:do not|don't|not asking|no need to).{0,20}(?:explain|ask|discuss)", clause, re.I)
                or not re.search(purpose, clause, re.I)):
            continue
        if re.search(r"在职证明|雇主信|工作证明|\b(?:employer(?:'s|’s)?|employment|company) letter\b|"
                     r"\bletter from (?:my|the|an?) employer\b", clause, re.I):
            kinds.add("employment")
        if re.search(r"行程(?:概要|说明|计划|安排|单|表)?|\bitinerary\b", clause, re.I):
            kinds.add("itinerary")
    return next(iter(kinds)) if len(kinds) == 1 else None


def is_document_purpose_question(body: str) -> bool:
    return _purpose_kind(body) is not None


def reviewed_document_purpose(body: str, language: str) -> str | None:
    kind = _purpose_kind(body)
    zh = language == "zh"
    if kind == "employment":
        return (
            "在职证明不只是说明收入，也帮助说明你目前的工作情况。"
            "雇主可以用公司抬头纸写明职位、薪资、任职时间和联系方式。"
            "如果假期已获批准，也可以说明请假和返岗安排，帮助解释旅行与工作的关系；"
            "这是结合情况整理材料的建议，不是所有访问申请都必须有固定格式的准假证明。"
            if zh else
            "An employer letter helps explain both your income and your current employment. "
            "Your employer can use company-headed paper to give your role, salary, length of employment "
            "and contact details. If leave is approved, noting the leave and return-to-work arrangements "
            "can help explain how the trip fits around your job. That is a preparation suggestion, "
            "not a universal requirement for a fixed-format leave letter."
        ) + "\nGOV.UK: " + DOCUMENTS_SOURCE + "#demonstrating-personal-circumstances"
    if kind == "itinerary":
        return (
            "行程概要用来说明你计划去哪里、做什么活动，以及时间怎样安排。"
            "可以把它当成核对工具：看看计划日期、住宿安排和旅行预算是否一致。"
            "这些也是在线申请会涉及的信息；行程概要是整理建议，不代表必须另交固定格式、逐小时的行程表。"
            "还没有预订的安排就写成计划，不要写成已经订好。"
            if zh else
            "A short itinerary explains your planned places, activities and timing. "
            "Use it to check that the dates, accommodation and trip budget fit together. "
            "The online application asks about those arrangements; a separate itinerary is an "
            "organising suggestion, not a universal requirement for a fixed-format or hourly schedule. "
            "Describe unbooked arrangements as plans, not confirmed bookings."
        ) + "\nGOV.UK: " + APPLICATION_SOURCE + "#documents-and-information-youll-need-to-apply"
    return None
