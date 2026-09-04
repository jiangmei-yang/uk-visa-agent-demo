"""Reviewed guidance for personal sponsor letters, funds and relationship evidence."""

from __future__ import annotations

import re

from visa_agent.domain.models import Case
from visa_agent.workflow.intent_matching import normalize_intent_text

SPONSOR_SOURCE = (
    "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/"
    "guide-to-supporting-documents-visiting-the-uk#if-you-have-a-sponsor"
)

_PERSONAL_RELATIONS_ZH = (
    r"我爸|爸爸|父亲|我妈|妈妈|母亲|父母|爸妈|配偶|丈夫|妻子|老公|老婆|伴侣|朋友|"
    r"家人|亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅|哥哥|姐姐|弟弟|妹妹|祖父母|祖父|祖母|"
    r"爷爷|奶奶|外公|外婆|表亲|堂亲|侄子|侄女|外甥|外甥女"
)
_PERSONAL_RELATIONS_EN = (
    r"dad|father|mum|mom|mother|parents?|spouse|husband|wife|partner|friend|family member|relative|"
    r"aunt|uncle|cousin|brother|sister|grandparents?|grandfather|grandmother|granddad|grandpa|"
    r"grandmum|grandma|niece|nephew|son|daughter|sibling"
)
_PERSONAL_RELATION_PATTERN = (
    rf"(?:{_PERSONAL_RELATIONS_ZH})|\b(?:my\s+)?(?:{_PERSONAL_RELATIONS_EN})\b"
)

SPONSOR_QUESTION_PATTERN = (
    r"(?:资助|担保|费用由.{0,8}承担).{0,28}(?:信|说明|材料|证明|写什么|怎么写|包含|提供)|"
    r"(?:资助信|资助说明|资助材料|资助证明).{0,28}(?:怎么|如何|写|包含|需要|准备|提供|具体)|"
    r"(?:资助人|担保人).{0,36}(?:关系|父子|父女|母子|母女|亲属).{0,18}(?:怎么|如何|用什么|材料|证明)|"
    r"(?:关系|父子|父女|母子|母女|亲属).{0,28}(?:怎么|如何|用什么|材料|证明).{0,24}(?:资助人|担保人)|"
    rf"(?:{_PERSONAL_RELATION_PATTERN}).{{0,22}}"
    r"(?:给我出钱|为我出钱|承担|支付|负责|资助).{0,18}(?:费用|旅费|机票|住宿|旅行)"
    r".{0,35}(?:需要|准备|提供|怎么|如何|材料|证明|证据|流水|关系|什么|哪些)|"
    r"\b(?:sponsor(?:ship)?|financial support).{0,35}(?:letter|statement|evidence|documents?|"
    r"write|include|contain|provide|prepare)\b|"
    r"\b(?:letter|statement|evidence|documents?).{0,35}\b(?:sponsor(?:ship)?|financial support)\b|"
    r"\b(?:sponsor|financial supporter).{0,35}\b(?:relationship|related|evidence|prove|show)\b|"
    r"\b(?:relationship|related|evidence|prove|show).{0,35}\b(?:sponsor|financial supporter)\b"
    rf"|\b(?:my\s+)?(?:{_PERSONAL_RELATIONS_EN})\b.{{0,24}}"
    r"(?:is|are|will be|plans? to be)?\s*(?:paying|pay|covering|cover|funding|fund)"
    r".{0,40}(?:what (?:do|does|should)|evidence|documents?|proof|need|provide|prepare)\b"
    r"|\b(?:what (?:evidence|documents?)|what (?:do|does|should)|how).{0,36}"
    rf"(?:my\s+)?(?:{_PERSONAL_RELATIONS_EN})\b.{{0,24}}"
    r"(?:paying|pay|covering|cover|funding|fund)\b"
)


def sponsor_support_question(text: str) -> bool:
    """Return true only for a direct question covered by the reviewed answer."""
    if not text or len(text) > 6000:
        return False
    text = normalize_intent_text(text)
    if re.search(
        r"(?:不用|不需要|不要|无需|别).{0,16}(?:资助|关系).{0,12}(?:说明|材料|证明|解释)|"
        r"\b(?:do not|don't|not asking|no need)\b.{0,25}\b(?:sponsor|relationship)\b",
        text,
        re.I,
    ):
        return False
    if sponsor_verification_question(text):
        return False
    negated_arrangement = re.search(
        rf"(?:{_PERSONAL_RELATION_PATTERN}).{{0,16}}"
        r"(?:不|不会|没有|没|无需|不需要).{0,8}(?:承担|支付|负责|资助|出钱)|"
        rf"\b(?:my\s+)?(?:{_PERSONAL_RELATIONS_EN})\b.{{0,18}}"
        r"(?:will not|won['’]t|does not|doesn['’]t|is not|isn['’]t)\s+"
        r"(?:pay|cover|fund|sponsor)",
        text,
        re.I,
    )
    # Negating one cost does not cancel a real sponsor question (for example,
    # "not flights, only accommodation; what should the letter say?").  Veto
    # only a wholly negated arrangement with no explicit sponsor-document ask.
    if negated_arrangement and not re.search(
        r"资助(?:信|说明|材料|证明)|担保(?:信|说明|材料)|"
        r"\b(?:sponsor(?:ship)?|financial support)\s+(?:letter|statement|evidence|documents?)\b",
        text,
        re.I,
    ):
        return False
    if re.search(
        r"(?:保证|确保|一定).{0,16}(?:获批|过签|拿到签证)|"
        r"(?:获批|过签|拿到签证).{0,16}(?:保证|确保|一定)|"
        r"\b(?:guarantee|ensure|promise).{0,20}(?:approval|approved|visa)|"
        r"\b(?:approval|approved|visa).{0,20}(?:guaranteed|certain)\b",
        text,
        re.I,
    ):
        return False
    if re.search(
        r"(?:我的)?(?:朋友|客户|同事|配偶|丈夫|妻子|老公|老婆|哥哥|姐姐|弟弟|妹妹|亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅)的.{0,22}(?:资助|担保)(?:信|说明|材料)|"
        r"(?:朋友|客户|同事|配偶|丈夫|妻子|老公|老婆|哥哥|姐姐|弟弟|妹妹|亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅).{0,18}(?:问|想知道|需要知道).{0,24}(?:资助|担保)|"
        r"(?:告诉|帮|替|给|为).{0,6}(?:我的?)?(?:朋友|客户|同事|配偶|丈夫|妻子|老公|老婆|哥哥|姐姐|弟弟|妹妹|亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅)"
        r".{0,20}(?:资助|担保)(?:信|说明|材料)|"
        r"\bmy\s+(?:friend|client|colleague|coworker|spouse|husband|wife|brother|sister|relative|aunt|uncle|cousin)['’]s\b.{0,28}"
        r"(?:sponsor|financial support)|"
        r"\b(?:friend|client|colleague|coworker|spouse|husband|wife|brother|sister|relative|aunt|uncle|cousin)\b.{0,18}"
        r"(?:asked|wants? to know).{0,28}\b(?:sponsor|financial support)\b|"
        r"\b(?:prepare|write|make).{0,24}\b(?:sponsor|financial support).{0,18}"
        r"\bfor\s+my\s+(?:friend|client|colleague|coworker|spouse|husband|wife|brother|sister|relative|aunt|uncle|cousin)\b|"
        r"\b(?:tell|help)\s+my\s+(?:friend|client|colleague|coworker|spouse|husband|wife|brother|sister|relative|aunt|uncle|cousin)\b"
        r".{0,24}\b(?:sponsor|financial support)\b",
        text,
        re.I,
    ):
        return False
    if re.search(SPONSOR_QUESTION_PATTERN, text, re.I):
        return True
    payer = bool(re.search(
        rf"{_PERSONAL_RELATION_PATTERN}|资助人|担保人|\b(?:my\s+)?sponsor\b",
        text,
        re.I,
    ))
    payment = bool(re.search(
        r"给我出钱|为我出钱|承担|支付|付款|资助|负责费用|"
        r"\b(?:pay|pays|paying|cover|covers|covering|fund|funds|funding|sponsor|sponsors|sponsoring)\b",
        text,
        re.I,
    ))
    evidence_request = bool(re.search(
        r"(?:需要|要|应该)(?:准备|提供|写|交)(?:什么|哪些)?|"
        r"(?:怎么|如何)(?:证明|说明|准备)|(?:材料|证明|证据|流水|关系)(?:怎么|如何|需要|用什么)|"
        r"\b(?:what (?:do|does|should)|what (?:evidence|documents?|proof)|how (?:do|does|can)|"
        r"need|provide|prepare|show|prove|evidence|documents?|proof)\b",
        text,
        re.I,
    ))
    return payer and payment and evidence_request


def sponsor_verification_question(text: str) -> bool:
    """Detect sponsor questions that the reviewed template cannot decide safely.

    The general template can explain what support evidence should communicate.  It
    cannot decide whether a particular formality or single document is mandatory,
    sufficient or capable of guaranteeing an outcome.  Keep this selector narrow
    so ordinary questions such as "what should the letter include?" still receive
    the useful reviewed answer.
    """
    if not text or len(text) > 6000:
        return False
    text = normalize_intent_text(text)
    sponsor_context = bool(re.search(
        rf"资助|担保|关系(?:材料|证明)|费用由.{0,10}(?:承担|支付)|"
        rf"\b(?:sponsor(?:ship)?|financial support|relationship evidence)\b|"
        rf"(?:{_PERSONAL_RELATION_PATTERN}).{{0,28}}(?:出钱|承担|支付|资助|材料|证明|"
        r"pay|cover|fund|evidence|documents?)",
        text,
        re.I,
    ))
    if not sponsor_context:
        return False
    return bool(re.search(
        r"公证|见证|律师|法律认证|认证翻译|"
        r"加密货币|虚拟货币|比特币|以太币|"
        r"(?:只|仅)(?:交|提供|准备|有|用).{0,18}(?:信|说明|材料|证明)|"
        r"(?:信|说明|材料|证明).{0,18}(?:就够|够不够|足够|可以了|唯一)|"
        r"(?:没有|不交|不提供|不需要|无)(?:任何)?(?:银行|资金|流水|对账单).{0,8}(?:材料|证明|证据)?|"
        r"(?:必须|一定要|强制|是否必须).{0,24}"
        r"(?:出生证明|结婚证|户口本|银行流水|对账单|资助信|担保信|材料|证明)|"
        r"(?:是否需要|要不要|需不需要).{0,24}"
        r"(?:出生证明|结婚证|户口本|银行流水|对账单|资助信|担保信)|"
        r"(?:出生证明|结婚证|户口本|银行流水|对账单|资助信|担保信).{0,18}"
        r"(?:必须|一定|强制|唯一|才行|才可以|是否需要|要不要|需不需要)|"
        r"(?:保证|确保|一定).{0,16}(?:获批|过签|拿到签证)|"
        r"(?:获批|过签|拿到签证).{0,16}(?:保证|确保|一定)|"
        r"\b(?:notari[sz](?:e|ed|ation)|notary|witness(?:ed|ing)?|solicitors?|lawyers?|legalis(?:e|ed|ation))\b|"
        r"\b(?:cryptocurrency|crypto(?:currency)?|bitcoin|ethereum)\b|"
        r"\bonly\b.{0,24}\b(?:letter|statement|document|evidence)\b|"
        r"\bjust\s+(?:a|the)\s+(?:letter|statement|document|piece of evidence)\b|"
        r"\b(?:letter|statement|document|evidence)\b.{0,24}\b(?:alone|only|enough|sufficient)\b|"
        r"\b(?:without|no)\s+(?:any\s+)?(?:bank|financial|funds?)\s+(?:evidence|documents?|statements?)\b|"
        r"\b(?:must|mandatory|required|have to)\b.{0,30}"
        r"\b(?:birth certificate|marriage certificate|bank statements?|sponsor(?:ship)? letter|document|evidence)\b|"
        r"\b(?:does|do|can|is|are)\b.{0,24}\bneed\b.{0,24}"
        r"\b(?:birth certificate|marriage certificate|bank statements?)\b|"
        r"\b(?:birth certificate|marriage certificate|bank statements?|sponsor(?:ship)? letter)\b.{0,24}"
        r"\b(?:must|required|mandatory|have to|the only|enough|sufficient)\b|"
        r"\b(?:guarantee|ensure|promise).{0,20}(?:approval|approved|visa)|"
        r"\b(?:approval|approved|visa).{0,20}(?:guaranteed|certain)\b",
        text,
        re.I,
    ))


def sponsor_verification_answer(body: str, language: str) -> str:
    """Give a scoped adviser answer for a sponsor formality/sufficiency question."""
    text = normalize_intent_text(body)
    guarantee = bool(re.search(
        r"(?:保证|确保|一定).{0,16}(?:获批|过签|拿到签证)|"
        r"(?:获批|过签|拿到签证).{0,16}(?:保证|确保|一定)|"
        r"\b(?:guarantee|ensure|promise).{0,20}(?:approval|approved|visa)|"
        r"\b(?:approval|approved|visa).{0,20}(?:guaranteed|certain)\b",
        text,
        re.I,
    ))
    formal = bool(re.search(
        r"公证|见证|律师|法律认证|"
        r"\b(?:notari[sz](?:e|ed|ation)|notary|witness(?:ed|ing)?|solicitors?|lawyers?|legalis(?:e|ed|ation))\b",
        text,
        re.I,
    ))
    crypto = bool(re.search(
        r"加密货币|虚拟货币|比特币|以太币|"
        r"\b(?:cryptocurrency|crypto(?:currency)?|bitcoin|ethereum)\b",
        text,
        re.I,
    ))
    relationship_document = bool(re.search(
        r"出生证明|结婚证|户口本|"
        r"\b(?:birth certificate|marriage certificate|household reg(?:istration|ister))\b",
        text,
        re.I,
    ))
    letter_alone = bool(re.search(
        r"(?:只|仅)(?:交|提供|准备|有|用).{0,18}(?:信|说明|材料|证明)|"
        r"(?:没有|不交|不提供|无)(?:任何)?(?:银行|资金|流水|对账单)|"
        r"\bonly\b.{0,24}\b(?:letter|statement|document|evidence)\b|"
        r"\bjust\s+(?:a|the)\s+(?:letter|statement|document|piece of evidence)\b|"
        r"\b(?:letter|statement|document|evidence)\b.{0,24}\b(?:alone|only|enough|sufficient)\b|"
        r"\b(?:without|no)\s+(?:any\s+)?(?:bank|financial|funds?)\s+(?:evidence|documents?|statements?)\b",
        text,
        re.I,
    ))

    if language == "zh":
        if guarantee:
            answer = (
                "资助说明不能保证签证获批；它只是整套申请材料的一部分。"
                "先核对说明是否写清资助人与你的关系、本次访问、承担的具体费用和付款方式，"
                "再分别配上能说明可用资金及来源、双方关系的真实材料；资助人在英国时，还要核对其合法身份或居留材料。"
                "最先做的一步：让资助人把“承担什么、怎样付款”书面确认，再逐项对应证明。"
            )
        elif formal:
            answer = (
                "这份访客材料指南没有规定所有资助说明都必须公证、由律师见证或认证，"
                "所以我不会建议你默认花这笔费用。先把实质内容做完整：写清双方关系、本次访问、"
                "承担的费用和付款方式，并配上相符的资金及关系材料。"
                "如果你因某份文件或当地签发要求收到明确的认证要求，再把那项要求单独核实。"
            )
        elif crypto:
            answer = (
                "目前这份已核验的访客材料指南不足以让我把加密资产直接判断为合适或充分的资助证明。"
                "不要只依赖加密资产；先整理能清楚显示资助人可用资金、资金来源和交易记录的常规金融材料。"
                "如果加密资产对资金解释不可或缺，应在提交前做人工个案复核。"
            )
        elif relationship_document:
            answer = (
                "出生证明、结婚证或户籍记录都不是适用于所有资助关系的唯一必交文件。"
                "应按真实关系选择现有且内容相符的记录，不要为了凑材料制作或强行套用某一种证明。"
                "最先做的一步：先写明资助人与申请人的实际关系，再盘点你们现有的官方关系记录。"
            )
        elif letter_alone:
            answer = (
                "只交一封资助说明，不能单独说明整项资助安排是否可靠。"
                "说明应写清关系、承担的费用和付款方式，同时用相符的材料解释资助人的可用资金及来源、双方关系；"
                "资助人在英国时，再核对其合法身份或居留材料。"
                "最先做的一步：把说明中的每一项承诺与一份真实现有的证明对应起来。"
            )
        else:
            answer = (
                "这里没有一套适用于所有申请人的“必交资助文件”答案。"
                "应核对材料能否说明谁提供支持、承担什么、怎样付款、双方关系以及资助人是否确有可用资金；"
                "资助人在英国时，还要核对其合法身份或居留材料。"
                "最先做的一步：先确认资助范围和付款方式，再按实际情况匹配证明。"
            )
    elif guarantee:
        answer = (
            "A sponsor letter cannot guarantee visa approval; it is one part of the application as a whole. "
            "Check that it identifies the relationship, the visit, the exact costs covered and how payment will work, "
            "then match it to genuine evidence of accessible funds, their source and the relationship. If the sponsor "
            "is in the UK, also check their lawful UK status evidence. First action: ask the sponsor to confirm in "
            "writing exactly what they will cover and how they will pay, then match each claim to evidence."
        )
    elif formal:
        answer = (
            "The Visitor supporting-document guide does not set a universal rule that every sponsor statement must be "
            "notarised, witnessed by a solicitor or legalised, so I would not add that cost by default. Make the substance "
            "clear first: the relationship, this visit, the exact costs covered and how payment will work, with matching "
            "financial and relationship evidence. If a document issuer or local process gives you a specific certification "
            "requirement, have that requirement checked separately."
        )
    elif crypto:
        answer = (
            "The reviewed Visitor guidance does not give me a reliable basis to treat cryptocurrency by itself as suitable "
            "or sufficient sponsor evidence. Do not rely on it alone. First gather conventional records that clearly show "
            "the sponsor's accessible funds, their source and transactions. If crypto is material to the funding explanation, "
            "the case should receive an individual manual review before submission."
        )
    elif relationship_document:
        answer = (
            "A birth certificate, marriage certificate or household record is not the one universal document for every "
            "sponsor relationship. Use genuine existing records that fit the actual relationship; do not create or force a "
            "document that does not apply. First action: state the real relationship, then list the official relationship "
            "records you already have."
        )
    elif letter_alone:
        answer = (
            "A sponsor letter on its own does not establish whether the whole support arrangement is evidenced. It should "
            "explain the relationship, exact costs and payment method, with matching evidence of the sponsor's accessible "
            "funds and their source, and the relationship. If the sponsor is in the UK, also check their lawful UK status "
            "evidence. First action: match each promise in the letter to genuine existing evidence."
        )
    else:
        answer = (
            "There is no single mandatory sponsor-document bundle that fits every applicant. Check whether the evidence "
            "shows who will provide support, what they will cover, how they will pay, the relationship and the sponsor's "
            "accessible funds. If the sponsor is in the UK, also check their lawful UK status evidence. First action: confirm "
            "the support scope and payment method, then match the evidence to the actual arrangement."
        )
    return answer + "\nGOV.UK: " + SPONSOR_SOURCE


def _relationship_label(body: str, case: Case | None, *, language: str) -> str:
    body = normalize_intent_text(body)
    relationship = case.profile.sponsor_relationship if case else None
    if re.search(
        r"父母|爸妈|爸爸妈妈|父亲和母亲|母亲和父亲|\bparents?\b|"
        r"\b(?:mother|mum|mom) and (?:father|dad)\b|\b(?:father|dad) and (?:mother|mum|mom)\b",
        body,
        re.I,
    ):
        relationship = "parents"
    elif re.search(r"父亲|爸爸|我爸|\bfather\b|\bdad\b", body, re.I):
        relationship = "father"
    elif re.search(r"母亲|妈妈|我妈|\bmother\b|\bmum\b|\bmom\b", body, re.I):
        relationship = "mother"
    elif re.search(r"配偶|丈夫|妻子|老公|老婆|\b(?:spouse|husband|wife)\b", body, re.I):
        relationship = "spouse"
    elif re.search(r"伴侣|\bpartner\b", body, re.I):
        relationship = "partner"
    elif re.search(r"朋友|\bfriend\b", body, re.I):
        relationship = "friend"
    elif re.search(r"雇主|公司|\bemployer\b", body, re.I):
        relationship = "employer"
    elif re.search(r"姐姐|妹妹|\bsister\b", body, re.I):
        relationship = "sister"
    elif re.search(r"哥哥|弟弟|\bbrother\b", body, re.I):
        relationship = "brother"
    elif re.search(
        r"亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅|祖父母|祖父|祖母|爷爷|奶奶|外公|外婆|"
        r"\b(?:relative|aunt|uncle|cousin|grandparents?|grandfather|grandmother|granddad|grandpa|grandmum|grandma|niece|nephew)\b",
        body,
        re.I,
    ):
        relationship = "relative"
    labels = {
        "father": ("父亲", "father"),
        "mother": ("母亲", "mother"),
        "parents": ("父母", "parents"),
        "spouse": ("配偶", "spouse"),
        "partner": ("伴侣", "partner"),
        "friend": ("朋友", "friend"),
        "employer": ("雇主", "employer"),
        "sister": ("姐妹", "sister"),
        "brother": ("兄弟", "brother"),
        "relative": ("亲属", "relative"),
    }
    return labels.get(str(relationship).casefold(), ("资助人", "sponsor"))[0 if language == "zh" else 1]


def _organisation_support(body: str, case: Case | None) -> bool:
    """Keep institutional funding out of the personal/relative template."""
    text = normalize_intent_text(body)
    individual = re.search(
        rf"{_PERSONAL_RELATION_PATTERN}|姐妹|兄弟",
        text,
        re.I,
    )
    organisation = re.search(
        r"雇主|公司|学校|大学|单位|机构|资助部门|"
        r"\b(?:employer|company|school|university|college|organisation|organization)\b",
        text,
        re.I,
    )
    if organisation and not individual:
        return True
    return bool(case and case.profile.funding_source == "employer_or_school" and not individual)


def _organisation_label(body: str, language: str) -> str:
    text = normalize_intent_text(body)
    if re.search(r"雇主|公司|\b(?:employer|company)\b", text, re.I):
        return "雇主" if language == "zh" else "employer"
    if re.search(r"学校|大学|\b(?:school|university|college)\b", text, re.I):
        return "学校" if language == "zh" else "school or university"
    return "资助单位" if language == "zh" else "funding organisation"


def _payment_method(body: str, language: str) -> str | None:
    text = normalize_intent_text(body)
    if re.search(r"直接(?:支付|付款|购买|预订|买)|代付|\b(?:pay|book|buy)\w* directly\b|\bdirect payment\b", text, re.I):
        return "直接支付" if language == "zh" else "pay directly"
    if re.search(r"报销|垫付后报销|\breimburse\w*\b", text, re.I):
        return "事后报销" if language == "zh" else "reimburse you"
    if re.search(r"转账|转给我|\btransfer\w*.{0,12}(?:me|applicant)\b", text, re.I):
        return "转给你" if language == "zh" else "transfer the money to you"
    return None


def concise_sponsor_preparation(
    body: str,
    language: str,
    case: Case,
    *,
    conference: bool = False,
) -> str:
    """Turn newly supplied funding facts into one consultant-style action."""
    costs = _covered_costs(body, language=language)
    method = _payment_method(body, language)
    if _organisation_support(body, case):
        organisation = _organisation_label(body, language)
        if language == "zh":
            opening = (
                f"按你说的安排，{organisation}会承担{costs}，并{method}。" if costs and method
                else f"按你说的安排，{organisation}会承担{costs}。" if costs
                else f"按你说的安排，{organisation}会{method}。" if method
                else f"既然这次由{organisation}资助，我会按单位资助来整理。"
            )
            if conference:
                return (
                    opening
                    + "这次是参会，邀请函和单位付款安排要讲同一件事。"
                    "最先只做一件事：请主办方出具邀请函，写清会议或活动名称、"
                    "时间地点和你参加的身份或内容。收到后，我们再用这些活动信息核对"
                    + organisation
                    + "的正式抬头说明，核对资助单位与你的关系、承担的具体费用、"
                    "付款或报销方式和可核实的联系人。邀请函解释为什么去英国，"
                    "但不能代替单位资助证明。"
                    "\nGOV.UK: " + SPONSOR_SOURCE
                )
            return (
                opening
                + " 下一步先请负责部门出具正式抬头说明，内容要和实际安排一致：单位与你的关系、"
                "这次赴英目的、承担的具体费用、怎样支付（例如直接支付还是事后报销），以及可核实的联系人。"
                "邀请函只证明受邀，"
                "不会自动证明对方承担费用。"
                "\nGOV.UK: " + SPONSOR_SOURCE
            )
        opening = (
            f"Based on what you said, the {organisation} will cover {costs} and {method}."
            if costs and method
            else f"Based on what you said, the {organisation} will cover {costs}." if costs
            else f"Based on what you said, the {organisation} will {method}." if method
            else f"Since the {organisation} is funding the trip, we'll prepare the evidence on that basis."
        )
        if conference:
            return (
                opening
                + " Because this is a conference trip, the invitation and the organisation's payment "
                "arrangement need to tell one consistent story. Your first action is one document: ask the "
                "organiser for an invitation stating the event name, dates and location, and your role or "
                "participation. Once it arrives, we can use those event details to check the "
                + organisation
                + "'s headed letter about its support: it should state its relationship to you, the exact costs covered, "
                "the payment or reimbursement method and a verifiable contact; the invitation explains why "
                "you are visiting the UK, but it does not replace evidence of the organisation's funding."
                "\nGOV.UK: " + SPONSOR_SOURCE
            )
        return (
            opening
            + " Ask the responsible department for a headed letter that matches the real arrangement: "
            "its relationship to you, the purpose of this UK trip, the exact costs covered, and how payment "
            "will work—for example, whether it pays directly or reimburses you—plus a verifiable contact. "
            "An invitation confirms attendance; it does not by itself prove funding."
            "\nGOV.UK: " + SPONSOR_SOURCE
        )

    relationship = _relationship_label(body, case, language=language)
    if language == "zh":
        opening = (
            f"按你说的安排，{relationship}会承担{costs}。" if costs
            else f"既然这次由{relationship}资助，我会按个人资助来整理。"
        )
        method_text = f"，并写明付款方式是{method}" if method else "，并说明怎样支付"
        status = "；如果资助人在英国，再配其英国合法身份或居留证明" if case.profile.sponsor_is_in_uk is not False else ""
        return (
            opening
            + f"下一步先请资助说明对应这次访问，写清{relationship}承担的具体费用{method_text}；"
            + f"资金材料与关系材料分开准备{status}。"
            "\nGOV.UK: " + SPONSOR_SOURCE
        )
    opening = (
        f"Based on what you said, your {relationship} will cover {costs}." if costs
        else f"Since your {relationship} is funding this trip, we'll prepare it as personal sponsorship."
    )
    method_text = f" and state that they will {method}" if method else " and explain how they will pay"
    status = ". If the sponsor is in the UK, add evidence of their lawful status in the UK" if case.profile.sponsor_is_in_uk is not False else ""
    return (
        opening
        + " Ask for a short statement tied to this visit and the exact costs"
        + method_text
        + ". Keep the sponsor's evidence of funds and your relationship evidence separate"
        + status
        + ".\nGOV.UK: " + SPONSOR_SOURCE
    )


def _covered_costs(body: str, *, language: str) -> str | None:
    body = normalize_intent_text(body)
    body = re.sub(
        r"爸爸和妈妈|妈妈和爸爸|父亲和母亲|母亲和父亲|"
        r"\b(?:my\s+)?(?:mother|mum|mom) and (?:my\s+)?(?:father|dad)\b|"
        r"\b(?:my\s+)?(?:father|dad) and (?:my\s+)?(?:mother|mum|mom)\b",
        "父母 parents",
        body,
        flags=re.I,
    )
    pay = (
        r"承担|支付|付款|付费|付钱|出钱|出资|买|订|预订|"
        r"负责(?:支付|承担|费用)|负责(?=.{0,3}(?:机票|住宿|酒店|生活费|餐费|交通))|报销|"
        r"pay(?:s|ing|ed)?|cover(?:s|ing|ed)?|fund(?:s|ing|ed)?|"
        r"buy(?:s|ing)?|bought|book(?:s|ing|ed)?"
    )
    entities = {
        "parents": r"父母|爸妈|\bparents?\b",
        "father": r"我爸|爸爸|父亲|\b(?:my\s+)?(?:father|dad)\b",
        "mother": r"我妈|妈妈|母亲|\b(?:my\s+)?(?:mother|mum|mom)\b",
        "friend": r"朋友|\b(?:my\s+)?friend\b",
        "relative": (
            r"亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅|哥哥|姐姐|弟弟|妹妹|祖父母|祖父|祖母|"
            r"爷爷|奶奶|外公|外婆|表亲|堂亲|侄子|侄女|外甥|外甥女|"
            r"\b(?:my\s+)?(?:relative|aunt|uncle|brother|sister|cousin|grandparents?|grandfather|"
            r"grandmother|granddad|grandpa|grandmum|grandma|niece|nephew|son|daughter|sibling)\b"
        ),
        "organisation": (
            r"雇主|公司|学校|大学|单位|机构|"
            r"\b(?:my\s+)?(?:employer|company|school|university|college|organisation|organization)\b"
        ),
    }
    payers = {
        name for name, pattern in entities.items()
        if re.search(rf"(?:{pattern}).{{0,18}}(?:{pay})|(?:{pay}).{{0,18}}(?:{pattern})", body, re.I)
    }
    if re.search(
        rf"(?:我自己|由我|我来|我会|我将)(?:.{{0,4}})(?:{pay})|"
        rf"我\s*(?:{pay})|"
        rf"(?:{pay}).{{0,8}}(?:我自己|由我|我来)|"
        rf"\bI\b.{{0,16}}(?:{pay})|(?:{pay}).{{0,16}}\bby me\b",
        body,
        re.I,
    ):
        payers.add("applicant")
    if len(payers) != 1:
        return None
    payer = next(iter(payers))
    payer_pattern = entities.get(payer)
    if payer_pattern is None:
        return None
    clauses = re.split(r"[。！？!?；;，,]|\b(?:but|while|whereas)\b|但|而", body, flags=re.I)
    any_entity = "|".join(f"(?:{pattern})" for pattern in entities.values())
    supported = [
        clause for clause in clauses
        if re.search(pay, clause, re.I)
        and (re.search(payer_pattern, clause, re.I) or not re.search(any_entity, clause, re.I))
    ]
    if not supported:
        return None
    body = "\n".join(supported)
    if re.search(
        r"(?:我爸|爸爸|父亲|我妈|妈妈|母亲|父母|资助人|担保人).{0,12}"
        r"(?:是否|能否|会不会|可不可以|该不该).{0,10}(?:承担|支付|负责|资助)|"
        r"(?:是否|能否|会不会|可不可以|该不该).{0,12}"
        r"(?:我爸|爸爸|父亲|我妈|妈妈|母亲|父母|资助人|担保人).{0,10}(?:承担|支付|负责|资助)?|"
        r"\b(?:does|can|could|should|will|would|may|might)\s+(?:my\s+)?"
        rf"(?:{_PERSONAL_RELATIONS_EN}|sponsor)\b.{{0,16}}"
        r"(?:pay|cover|fund|sponsor)\b",
        body,
        re.I,
    ):
        return None
    if re.search(
        r"(?:资助信|资助说明|担保信).{0,28}(?:是否|要不要|该不该).{0,24}(?:承担|支付|包括)|"
        r"\b(?:should|must).{0,30}(?:letter|statement).{0,30}(?:say|state|include)"
        r".{0,18}\bwhether\b",
        body,
        re.I,
    ):
        return None

    def negated(match: re.Match[str]) -> bool:
        before = body[max(0, match.start() - 40):match.start()]
        after = body[match.end():match.end() + 28]
        return bool(
            re.search(
                r"(?:不|不会|并不|并非|没(?:有)?|无需|不需要|不打算)\s*"
                r"(?:承担|负责|支付|资助|报销)?\s*$",
                before,
            )
            or re.search(
                r"(?:\bnot\b|won['’]t|will not|doesn['’]t|does not|isn['’]t|is not)"
                r"(?:\s+\w+){0,3}\s*(?:cover(?:ing)?|pay(?:ing)?|fund(?:ing)?)?\s*$",
                before,
                re.I,
            )
            or re.search(
                r"^\s*(?:不|不会|并不|并非|没(?:有)?|无需|不需要)\s*"
                r"(?:承担|负责|支付|资助|报销)?",
                after,
            )
            or re.search(
                r"^\s*(?:is|are|will be|would be)?\s*"
                r"(?:\bnot\b|won['’]t|will not|isn['’]t|is not)\s*"
                r"(?:covered?|paid?|funded?)?",
                after,
                re.I,
            )
        )

    found = []
    patterns = [
        (r"机票|flights?|airfare", "机票", "flights"),
        (r"住宿|酒店|accommodation|hotels?", "住宿", "accommodation"),
        (r"生活费|餐费|maintenance|living costs?|meals?", "在英期间的生活费", "living costs in the UK"),
        (r"交通|local transport", "当地交通", "local transport"),
    ]
    for pattern, zh, en in patterns:
        matches = list(re.finditer(pattern, body, re.I))
        positive = any(not negated(match) for match in matches)
        if positive:
            found.append(zh if language == "zh" else en)
    if not found:
        return None
    connector = "、" if language == "zh" else " and "
    return connector.join(found)


def _payer_cost_allocations(body: str, *, language: str) -> list[tuple[str, str]]:
    """Extract only independently stated payer/cost clauses.

    This is deliberately used only when two or more payers are explicit.  It
    prevents a single-sponsor template from erasing or merging the other payer.
    """
    text = normalize_intent_text(body)
    entities = [
        (r"父亲|爸爸|我爸|\b(?:my\s+)?(?:father|dad)\b", "父亲", "father"),
        (r"母亲|妈妈|我妈|\b(?:my\s+)?(?:mother|mum|mom)\b", "母亲", "mother"),
        (r"朋友|\b(?:my\s+)?friend\b", "朋友", "friend"),
        (r"雇主|公司|\b(?:my\s+)?(?:employer|company)\b", "雇主", "employer"),
        (r"学校|大学|\b(?:my\s+)?(?:school|university|college)\b", "学校", "school or university"),
        (r"姐姐|妹妹|\b(?:my\s+)?sister\b", "姐妹", "sister"),
        (r"哥哥|弟弟|\b(?:my\s+)?brother\b", "兄弟", "brother"),
        (
            r"亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅|祖父母|祖父|祖母|爷爷|奶奶|外公|外婆|"
            r"\b(?:my\s+)?(?:relative|aunt|uncle|cousin|grandparents?|grandfather|grandmother|"
            r"granddad|grandpa|grandmum|grandma|niece|nephew|son|daughter|sibling)\b",
            "亲属",
            "relative",
        ),
    ]
    allocations: list[tuple[str, str]] = []
    split_pattern = (
        r"[。！？!?;；，,]|\b(?:but|while|whereas)\b|但|而|"
        r"\band\b(?=\s+(?:my\s+)?(?:father|dad|mother|mum|mom|friend|employer|company|"
        r"school|university|college|sister|brother|relative|aunt|uncle|cousin)\b.{0,16}"
        r"(?:pay|cover|fund|buy|book))|"
        r"和(?=(?:我)?(?:父亲|爸爸|母亲|妈妈|朋友|雇主|公司|学校|大学|"
        r"姐姐|妹妹|哥哥|弟弟|亲属|姑姑|姑妈|姨妈|阿姨|叔叔|舅舅).{0,12}"
        r"(?:承担|支付|付|出|负责|报销|买|订))"
    )
    for clause in re.split(split_pattern, text, flags=re.I):
        matched = [(pattern, zh, en) for pattern, zh, en in entities if re.search(pattern, clause, re.I)]
        if len(matched) != 1:
            continue
        costs = _covered_costs(clause, language=language)
        if costs is None and re.search(r"出(?:费|钱)?", clause) and re.search(
            r"机票|住宿|酒店|生活费|餐费|交通", clause,
        ):
            labels = [
                zh if language == "zh" else en
                for pattern, zh, en in (
                    (r"机票", "机票", "flights"),
                    (r"住宿|酒店", "住宿", "accommodation"),
                    (r"生活费|餐费", "在英期间的生活费", "living costs in the UK"),
                    (r"交通", "当地交通", "local transport"),
                )
                if re.search(pattern, clause)
            ]
            costs = ("、" if language == "zh" else " and ").join(labels) or None
        if costs:
            _, zh, en = matched[0]
            allocations.append((zh if language == "zh" else en, costs))
    # Preserve order, merging repeated clauses for the same payer without
    # inventing a combined allocation for anyone else.
    merged: dict[str, list[str]] = {}
    for payer, costs in allocations:
        merged.setdefault(payer, [])
        for cost in re.split(r"、|\s+and\s+", costs):
            if cost not in merged[payer]:
                merged[payer].append(cost)
    connector = "、" if language == "zh" else " and "
    return [(payer, connector.join(costs)) for payer, costs in merged.items()]


def sponsor_support_answer(body: str, language: str, case: Case | None = None) -> str:
    """Build a useful answer without deciding sufficiency or requiring one document type."""
    conditional = bool(re.search(
        r"(?:如果|假如|假设)|\b(?:if|assuming|suppose)\b",
        normalize_intent_text(body),
        re.I,
    ))
    contextual_case = None if conditional else case
    explicit_relationship = _relationship_label(body, None, language="en")
    if (contextual_case and explicit_relationship != "sponsor"
            and contextual_case.profile.sponsor_relationship != explicit_relationship):
        contextual_case = None
    relationship = _relationship_label(body, contextual_case, language=language)
    costs = _covered_costs(body, language=language)
    allocations = _payer_cost_allocations(body, language=language)
    in_uk = contextual_case.profile.sponsor_is_in_uk if contextual_case else None
    if len(allocations) > 1:
        if language == "zh":
            detail = "，".join(f"{payer}承担{items}" for payer, items in allocations)
            opening = (f"如果你说的分工是{detail}，需要把两份支持分开说明。"
                       if conditional else f"按你说的分工，{detail}。")
            return (
                opening
                + "\n\n这种情况不要用一句“全部资助”合并。请每位资助人分别说明："
                "与你的关系、对应哪次赴英访问、自己承担的具体费用、怎样付款，"
                "以及愿意且能够提供支持。每人的资金材料和关系材料也要分开对应；"
                "如果其中某位资助人在英国，再补该人的英国合法身份或居留证明。\n\n"
                "最先做的一步：把每位资助人承担的费用和付款方式分别确认，再各自准备说明。"
                "\nGOV.UK: " + SPONSOR_SOURCE
            )
        detail = ", while ".join(
            f"your {payer} will cover {items}" for payer, items in allocations
        )
        opening = (f"If the arrangement is that {detail}, keep the two sources of support separate."
                   if conditional else f"Based on what you said, {detail}.")
        return (
            opening
            + "\n\nDo not merge this into one statement of 'full support'. Ask each sponsor to state separately "
            "their relationship to you, the UK visit involved, the exact costs they cover, how they will pay, "
            "and that they are willing and able to provide that support. Match each person's own financial and "
            "relationship evidence to their statement. If either sponsor is in the UK, add that person's evidence "
            "of lawful UK status.\n\n"
            "Your first practical step: confirm each sponsor's costs and payment method separately, then prepare the two statements."
            "\nGOV.UK: " + SPONSOR_SOURCE
        )
    if _organisation_support(body, case):
        organisation = _organisation_label(body, language)
        if language == "zh":
            opening = (
                f"如果由{organisation}承担{costs}，资助说明应把费用范围和付款方式写清楚。"
                if conditional and costs else
                f"按你说的安排，{organisation}将承担{costs}。"
                if costs else
                f"由{organisation}出资时，重点是把这次行程、承担范围和付款方式说清楚。"
            )
            return (
                opening
                + "\n\n一个实用写法是，请负责部门用正式抬头文件说明：\n"
                "1. 单位与你的关系，以及这次赴英的目的；\n"
                "2. 具体承担哪些费用，不要只写“全部资助”；\n"
                "3. 直接付款、预付还是事后报销；\n"
                "4. 可以核实的联系人、职务和联系方式；\n"
                "5. 单位愿意且能够提供这项支持。\n\n"
                "另外配与这次安排相符的会议、课程、派遣或批准记录。"
                "只说明受邀的邀请函，不会自动证明费用由对方承担；"
                "你的个人资金材料是否还需要，要看单位实际承担的范围。\n\n"
                "最先做的一步：请资助部门书面确认“承担哪些费用、怎样付款”，收到后再核对其他材料。"
                "\nGOV.UK: " + SPONSOR_SOURCE
            )
        opening = (
            f"If the {organisation} covers {costs}, its support letter should make the scope and payment method clear."
            if conditional and costs else
            f"Based on what you said, the {organisation} will cover {costs}."
            if costs else
            f"With funding from the {organisation}, the important point is to explain this trip, the exact support and how payment will work."
        )
        return (
            opening
            + "\n\nA practical way to structure it is to ask the responsible department for a headed letter that states:\n"
            "1. The organisation's relationship to you and the purpose of this UK trip.\n"
            "2. The exact costs it will cover, rather than only saying 'full support'.\n"
            "3. Whether it will pay directly, advance funds or reimburse you.\n"
            "4. A verifiable contact person's role and contact details.\n"
            "5. That it is willing and able to provide the stated support.\n\n"
            "Add records that match the arrangement, such as the relevant conference, course, assignment or funding approval. "
            "An invitation that only confirms attendance does not by itself show that the organisation will pay. "
            "Whether personal financial evidence is also useful depends on the costs the organisation actually covers.\n\n"
            "Your first practical step: ask the funding department to confirm in writing which costs it covers and how it will pay, then check the other evidence."
            "\nGOV.UK: " + SPONSOR_SOURCE
        )
    if language == "zh":
        opening = (
            f"如果由{relationship}承担{costs}，建议把资助范围和付款方式写清楚。"
            if conditional and costs else
            f"按你说的安排，{relationship}准备承担{costs}。"
            if costs else f"这部分要把{relationship}的资助承诺、资金和你们的关系对起来。"
        )
        status = ""
        if in_uk is True:
            status = "\n- 因为资助人在英国，还要准备其在英国的合法身份或居留证明。"
        elif in_uk is None:
            status = "\n- 如果资助人在英国，再加上其在英国的合法身份或居留证明。"
        return (
            opening
            + "\n\n资助说明不用写成法律文书。一个实用写法是说清以下五点：\n"
            "1. 资助人和申请人分别是谁，双方是什么关系；\n"
            "2. 对应的是哪一次赴英访问；\n"
            "3. 具体承担哪些费用，不要只写“全力资助”；\n"
            "4. 怎样支付，例如直接付款、报销或转给申请人；\n"
            "5. 资助人愿意且能够提供这些支持。\n\n"
            "再把其他材料分开准备：\n"
            "- 资金材料要能说明资助人可用的资金和来源，也要考虑其自身及受其扶养人的开支；"
            "没有一个适用所有访客的固定余额或流水月数。\n"
            f"- {_relationship_evidence_zh(relationship)}"
            + status
            + "\n\n最先做的一步：请资助人先确认“具体承担什么、怎样支付”，再写资助说明。"
            "\nGOV.UK: " + SPONSOR_SOURCE
        )

    possessive = "parents'" if relationship == "parents" else f"{relationship}'s"
    opening = (
        f"If your {relationship} covers {costs}, the statement should make the scope of support and payment method clear."
        if conditional and costs else
        f"Based on what you said, your {relationship} "
        f"{'plan' if relationship == 'parents' else 'plans'} to cover {costs}."
        if costs else
        f"The aim is to connect your {possessive} promise of support to the funds and your relationship."
    )
    status = ""
    if in_uk is True:
        status = "\n- Because the sponsor is in the UK, also prepare evidence of their lawful UK status."
    elif in_uk is None:
        status = "\n- If the sponsor is in the UK, also include evidence of their lawful UK status."
    return (
        opening
        + "\n\nThe sponsor statement does not need legalistic wording. A practical structure is to set out:\n"
        "1. Who the sponsor and applicant are, and their relationship.\n"
        "2. Which UK visit the support relates to.\n"
        "3. The exact costs covered, rather than only saying 'full support'.\n"
        "4. How payment will work, such as direct payment, reimbursement or a transfer to the applicant.\n"
        "5. That the sponsor is willing and able to provide that support.\n\n"
        "Prepare the other evidence separately:\n"
        "- Financial records should explain accessible funds and their source, while allowing for the sponsor's own and "
        "dependants' costs. There is no one fixed balance or statement period for every visitor.\n"
        f"- {_relationship_evidence_en(relationship)}"
        + status
        + "\n\nYour first practical step: ask the sponsor to confirm exactly what they will cover and how they will pay, then draft the statement."
        "\nGOV.UK: " + SPONSOR_SOURCE
    )


def _relationship_evidence_zh(relationship: str) -> str:
    if relationship in {"父亲", "母亲", "父母", "姐妹", "兄弟"}:
        example = "例如实际持有且内容匹配的出生证明或官方户籍记录。"
    elif relationship == "配偶":
        example = "例如实际持有且内容匹配的结婚或民事伴侣记录。"
    else:
        example = "选择与实际关系及本次安排相符的现有记录。"
    return (
        f"关系材料要用真实现有记录说明你与{relationship}的关系；{example}"
        "官方指南没有把某一种文件设成所有人唯一的关系证明，不要为了凑材料而编造。"
    )


def _relationship_evidence_en(relationship: str) -> str:
    if relationship in {"father", "mother", "parents", "sister", "brother"}:
        example = "For example, use a birth record or official household record only if it actually applies and matches. "
    elif relationship == "spouse":
        example = "For example, use an existing marriage or civil-partnership record only if it actually applies and matches. "
    else:
        example = "Choose genuine existing records that fit the actual relationship and this support arrangement. "
    person = "the sponsor" if relationship == "sponsor" else f"your {relationship}"
    return (
        f"Use genuine existing records to show your relationship to {person}. {example}"
        "The official guide does not prescribe one universal relationship document; do not create evidence simply to fill the gap."
    )
