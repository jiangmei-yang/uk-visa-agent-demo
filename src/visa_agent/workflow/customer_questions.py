"""Small reviewed answer set, not open-ended immigration advice."""

import re
from datetime import date

SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"
CHECKED_AT = date(2026, 9, 4)
REVIEW_AFTER = date(2026, 10, 4)


def grounded_customer_answers(body: str, language: str, today: date) -> list[str]:
    booking = re.search(r"机票|酒店|住宿|flight|hotel|accommodation", body, re.I)
    question = re.search(
        r"(?:需要|必须|要不要|是否|能否|可以|要先).{0,12}(?:买|订|预订).{0,4}(?:机票|酒店|住宿)|"
        r"(?:机票|酒店|住宿|预订).{0,8}(?:必须|需要|要先|要不要|证明|材料|证据).{0,12}(?:[?？]|吗)|"
        r"(?:必须|需要|要不要|是否).{0,5}(?:买|订)(?:吗|[?？])|"
        r"(?:do i|must i|should i|have to|need to).{0,25}(?:book|buy|reserve|flight|hotel)|"
        r"(?:flight|hotel|booking).{0,20}(?:required|necessary|evidence|proof).{0,12}\?",
        body, re.I,
    )
    if not booking or not question:
        return []
    if not CHECKED_AT <= today <= REVIEW_AFTER:
        return [
            "关于提前订机票和酒店的问题，我需要先复核最新官方说明，暂时不能给你确定答复。"
            if language == "zh"
            else "I need to recheck the current official guidance before answering your booking question."
        ]
    if re.search(r"过境|transit", body, re.I):
        return [
            "你提到过境；过境与普通访问的材料要求不能直接混用，需要先由顾问确认路线。"
            if language == "zh"
            else "You mentioned transit; its evidence requirements need a separate route check."
        ]
    answer = (
        "关于机票和酒店：普通 Standard Visitor 申请不需要为了提供这些预订证明而先购买。"
        "官方材料指南把酒店预订和机票预订（过境除外）列为不应作为证据提交的材料。"
        "我们先整理真实的计划行程和住宿安排；不要把尚未确定的安排写成已经预订。"
        if language == "zh"
        else "For an ordinary Standard Visitor application, you do not need to buy flights or book "
        "a hotel just to supply booking evidence. The official guide lists hotel bookings and "
        "flight bookings (except transit) as documents not to use as evidence. We can first "
        "record your intended arrangements without describing unbooked plans as confirmed bookings."
    )
    return [answer + "\nGOV.UK: " + SOURCE + "#documents-you-should-not-use-as-evidence"]
