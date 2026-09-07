"""Public guidance before personal-data processing; no model, files or profile writes."""

import re
from datetime import date

from visa_agent.privacy.customer_copy import reply_language


def public_consultation(body: str, today: date) -> str | None:
    from visa_agent.workflow.conversation import latest_reply_text
    from visa_agent.workflow.customer_questions import grounded_customer_answer_plan
    from visa_agent.workflow.guidance_freshness import CHECKED_AT, REVIEW_AFTER

    text = latest_reply_text(body).strip()
    if not text or len(text) > 700:
        return None
    # Requests to assess an individual's records are not public consultation.
    if re.search(
        r"\d{4}|@|护照号|身份证|出生|生日|我叫|姓名|住址|账号|账户余额|我的材料|附件|帮我核对|"
        r"\b(?:my name|date of birth|DOB|passport number|account number|my balance|attached|attachment|my documents|check my|review my)\b|"
        r"我(?:是|在|目前|持有|有|的)|\bI (?:am|have|hold|live|earn)|\bmy (?:passport|income|salary|sponsor)\b|"
        r"同意|授权|隐私|资料处理|\b(?:consent|privacy|processing|provider|model)\b", text, re.I,
    ):
        return None
    language = reply_language(text)
    introductory = bool(re.fullmatch(
        r"(?:你好[，, ]*)?(?:您好[，, ]*)?(?:我想|想|请问)?(?:办理|办|申请)?英国(?:旅游|访客|访问)?签证[，, ]*"
        r"(?:需要|要|应该准备|要准备|需要准备|需要提供)(?:哪些|什么|一些什么)?(?:资料|材料)[？?。！!]*|"
        r"(?:hello[, ]*)?what (?:documents|materials) (?:do I need|are needed) (?:for|to apply for) "
        r"(?:a |the )?(?:UK|British)(?: visitor| tourist)? visa[?.!]*", text, re.I,
    ))
    holiday = bool(re.fullmatch(r"(?:去)?旅游[。！!]*|(?:a )?holiday[.!]*|tourism[.!]*", text, re.I))
    # Only use purpose inside the already full-matched public enquiry, never a
    # keyword in quoted, negated, personal or unrelated text.
    holiday = holiday or (introductory and bool(re.search(r"旅游|\btourist\b", text, re.I)))
    if introductory or holiday:
        if not CHECKED_AT <= today <= REVIEW_AFTER:
            return ("可以先了解准备流程。具体材料要求我需要先复核最新官方说明；暂时不用发送证件或账户资料。"
                    if language == "zh" else
                    "We can start with general preparation. I need to recheck the current official guidance before listing requirements; please do not send identity or account documents yet.")
        answer = (
            "可以，先不用急着把材料一次发齐。英国访客签证通常需要说明出行目的、行程安排、怎样承担费用，以及目前的工作或学习情况；具体材料要按你的情况确定。\n\n"
            "可以先看看官方材料指引：https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk\n\n"
            "这次主要是旅游、探亲访友，还是参加会议？暂时不用发送护照或银行账户资料。"
            if language == "zh" else
            "You do not need to send everything at once. For a UK visitor application, preparation usually covers the purpose and plans for the visit, how costs will be covered, and your current work or study circumstances. The documents depend on your situation.\n\n"
            "Official supporting-document guidance: https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk\n\n"
            "Is the visit mainly for a holiday, visiting family or friends, or a conference? Please do not send passport or bank-account documents yet."
        )
        if holiday:
            answer = answer.replace(
                "这次主要是旅游、探亲访友，还是参加会议？暂时不用发送护照或银行账户资料。",
                "旅游的话，可以先整理大致行程和费用安排，日期没定也不用急着订票。你可以继续问申请入口、办理时间或翻译要求；暂时不用发送护照或银行账户资料。",
            ).replace(
                "Is the visit mainly for a holiday, visiting family or friends, or a conference? Please do not send passport or bank-account documents yet.",
                "For a holiday, start with a rough itinerary and how costs will be covered. You can ask about the application link, timing or translations before sharing personal documents.",
            )
        return answer
    plan = grounded_customer_answer_plan(text, language, today)
    public_topics = {"application", "application_link", "timing", "fees", "translation", "booking", "bank_period", "biometrics"}
    if (plan.answers and plan.selected_topics and not plan.omitted_topics
            and set(plan.selected_topics) <= public_topics):
        return "\n\n".join(plan.answers)
    return None
