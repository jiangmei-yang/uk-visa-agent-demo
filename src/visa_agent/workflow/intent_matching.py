"""Conservative text normalization used only for intent matching.

The original applicant text remains the evidence and is never rewritten. This
small table covers common Hong Kong Traditional Chinese variants in the demo's
supported consultation intents; it is not a general translation system.
"""

import re
import unicodedata

_TRADITIONAL_TO_SIMPLIFIED = str.maketrans({
    "請": "请", "資": "资", "訊": "讯", "總": "总", "這": "这", "網": "网",
    "頁": "页", "裡": "里", "簽": "签", "證": "证", "確": "确", "應": "应",
    "準": "准", "備": "备", "麼": "么", "說": "说", "給": "给", "擔": "担",
    "關": "关", "係": "系", "費": "费", "負": "负", "責": "责", "學": "学",
    "遊": "游", "還": "还", "個": "个", "種": "种", "嗎": "吗", "與": "与",
    "為": "为", "門": "门", "開": "开", "現": "现", "實": "实", "將": "将",
    "辦": "办", "會": "会", "連": "连", "鏈": "链", "結": "结", "發": "发",
    "離": "离", "抵": "抵", "後": "后", "時": "时", "點": "点", "獲": "获",
    "批": "批", "國": "国", "們": "们", "從": "从", "了": "了", "飛": "飞",
    "並": "并", "一": "一", "甚": "什", "該": "该", "屬": "属", "據": "据",
    "親": "亲", "機": "机", "寫": "写", "哪": "哪", "錄": "录", "帳": "账",
    "標": "标", "訪": "访", "歐": "欧", "聯": "联", "電": "电", "許": "许",
    "權": "权", "續": "续", "倫": "伦", "護": "护", "歷": "历", "區": "区",
    "術": "术", "畢": "毕", "業": "业", "計": "计", "劃": "划", "創": "创",
    "盟": "盟", "處": "处", "長": "长", "顧": "顾", "問": "问", "際": "际",
    "運": "运", "動": "动", "員": "员", "級": "级", "專": "专", "擴": "扩",
    "務": "务", "傭": "佣", "調": "调", "協": "协", "議": "议", "節": "节",
    "歸": "归", "體": "体",
})


def normalize_intent_text(text: str) -> str:
    """Normalize presentation variants without creating evidence or facts."""
    normalized = unicodedata.normalize("NFKC", text).translate(_TRADITIONAL_TO_SIMPLIFIED)
    # Hong Kong Traditional commonly uses 連結 for a web link. Character-wise
    # conversion yields 连结, while the reviewed matchers intentionally use 链接.
    return normalized.replace("连结", "链接")


EXPLICIT_VISITOR_ROUTE_PATTERN = (
    r"(?:英国)?(?:(?:普通|标准)?(?:访问|访客)|旅游)签证|"
    r"\b(?:(?:UK|British)\s+)?(?:(?:standard\s+)?visitor|tourist)(?:\s+visa|\s+route)?\b|"
    r"\bStandard Visitor\b"
)

_NAMED_NONVISITOR_ROUTE_PATTERN = (
    r"\b(?:student|work|skilled worker|graduate|spouse|family|marriage|child student|"
    r"global talent|innovator founder|high potential individual|ancestry|health and care worker|"
    r"scale[- ]?up|temporary work|frontier worker|adult dependent relative|parent|partner|fianc(?:e|é)|"
    r"family reunion|Hong Kong BN\(O\)|transit|marriage visitor)\s+(?:visa|route)\b|"
    r"(?:学生|留学|工作|技术工人|毕业生|配偶|家庭|结婚|儿童学生|全球人才|创新创始人|"
    r"高潜力人才|医疗护理工作|临时工作|边境工人|成年受抚养亲属|父母|伴侣|未婚夫妇|家庭团聚|过境|结婚访客|"
    r"国际(?:运动员|体育人士)|海外(?:家政工人|家庭佣工)|英国(?:祖籍|血统)|"
    r"高级(?:或)?专业工人|英国扩展工人|青年流动(?:计划|方案)|全球商务流动|"
    r"宗教(?:部长|领袖|工作者)|海外(?:企业|业务)代表|返英居民|回归居民|"
    r"慈善工作者|创意工作者|政府授权交流|服务供应商|借调工人|国际协议|季节性工人)"
    r"(?:签证|路线)"
)

_NONVISA_IMMIGRATION_CATEGORY_PATTERN = (
    r"\b(?:ETA|electronic travel authori[sz]ation|EU Settlement Scheme|EUSS|ILR|Tier\s*[1245]|"
    r"indefinite leave(?: to remain)?|British citizenship|naturalisation|naturalization|"
    r"family permit|right of abode|eVisa|Global Business Mobility|BN\(O\)|BNO)\b|"
    r"电子旅行(?:许可|授权)|欧盟定居计划|永久居留|永居|英国公民(?:身份)?|入籍|家庭许可|居留权|电子签证|"
    r"(?:法国|加拿大|澳洲|澳大利亚|美国|新西兰)(?:签证|路线)"
)

_ROUTE_LABEL_WORD = r"[A-Za-z0-9][A-Za-z0-9()'’.,&/-]*"
_STRUCTURAL_ROUTE_PATTERNS = (
    re.compile(
        rf"\b(?:for|under)\s+(?:an|a|the|my|this|that)?\s*"
        rf"({_ROUTE_LABEL_WORD}(?:\s+(?!visa\b|route\b){_ROUTE_LABEL_WORD}){{0,7}})\s+"
        r"(?:visa|route)\b",
        re.I,
    ),
    re.compile(
        rf"\b(?:an|a|the|my|this|that)\s+"
        rf"({_ROUTE_LABEL_WORD}(?:\s+(?!visa\b|route\b){_ROUTE_LABEL_WORD}){{0,7}})\s+"
        r"(?:visa|route)\b",
        re.I,
    ),
    re.compile(
        rf"^\s*({_ROUTE_LABEL_WORD}(?:\s+(?!visa\b|route\b){_ROUTE_LABEL_WORD}){{0,7}})\s+"
        r"(?:visa|route)\b",
        re.I,
    ),
    re.compile(
        r"\b([A-Z0-9][A-Za-z0-9()'’.,&/-]*"
        r"(?:\s+(?:(?:or|and|of|the)\s+)?[A-Z0-9][A-Za-z0-9()'’.,&/-]*){0,7})\s+"
        r"(?:visa|route)\b",
    ),
)

_NON_ROUTE_LABEL_WORDS = {
    "a", "an", "application", "apply", "continue", "continued", "continuing", "do", "does", "fee",
    "had", "has", "have", "how", "i", "is", "my", "please", "preparation", "prepare",
    "current", "existing", "expired", "new", "old", "previous", "prior", "refusal", "resume",
    "should", "the", "this", "valid", "visa", "want", "we", "what", "where",
}


def _structural_nonvisitor_matches(text: str) -> list[re.Match[str]]:
    matches: list[re.Match[str]] = []
    allowed_visitor_labels = {
        "visitor", "standard visitor", "ordinary visitor", "uk visitor", "british visitor",
        "uk standard visitor", "british standard visitor", "tourist", "uk tourist", "british tourist",
        "ordinary uk visitor", "ordinary british visitor", "uk ordinary visitor", "british ordinary visitor",
        "uk", "british",
    }
    for pattern in _STRUCTURAL_ROUTE_PATTERNS:
        for match in pattern.finditer(text):
            label = re.sub(r"\s+", " ", match[1]).strip(" ,.").casefold()
            words = set(re.findall(r"[a-z]+", label))
            if not words or words & _NON_ROUTE_LABEL_WORDS:
                continue
            visitor_words = {"visitor", "tourist"}.intersection(words)
            ordinary_visitor_words = {
                "visitor", "tourist", "standard", "ordinary", "uk", "british", "month", "months",
                "year", "years", "six", "two", "five", "ten", "upcoming", "regular", "normal",
                "intended", "proposed", "long", "term", "official",
            }
            if label in allowed_visitor_labels or (visitor_words and words <= ordinary_visitor_words):
                continue
            # A modified Visitor label (for example Marriage Visitor) is a
            # different category; merely containing the word visitor is not enough.
            matches.append(match)
    return matches


def explicit_visitor_route(text: str) -> bool:
    """Whether this clause expressly names the supported Visitor route."""
    normalized = normalize_intent_text(text)
    return bool(
        re.search(EXPLICIT_VISITOR_ROUTE_PATTERN, normalized, re.I)
        and not explicit_nonvisitor_route(normalized)
    )


def explicit_nonvisitor_route(text: str) -> bool:
    """Identify an affirmative, expressly named route outside this demo.

    This shared boundary protects both narrow FAQs and full-checklist rescue.
    It deliberately does not treat ordinary phrases such as ``visa preparation``
    or ``visa refusal`` as route names.
    """
    normalized = normalize_intent_text(text)
    competing = (
        _NAMED_NONVISITOR_ROUTE_PATTERN
        + r"|\bYouth Mobility Scheme\b|"
        + _NONVISA_IMMIGRATION_CATEGORY_PATTERN
    )
    matches = [*re.finditer(competing, normalized, re.I), *_structural_nonvisitor_matches(normalized)]
    for match in sorted(matches, key=lambda item: item.start()):
        prefix = normalized[:match.start()]
        window = normalized[max(0, match.start() - 48):match.end()]
        if re.search(
            r"(?:(?:不是|并非)(?:要|想|在)?(?:申请|办理)?|"
            r"不(?:申请|办理)|不打算(?:申请|办理)|不想(?:申请|办理))\s*$|"
            r"\b(?:not\s+(?:(?:applying|going to apply)\s+for\s+)?(?:a\s+|the\s+)?|"
            r"(?:don['’]t|do not)\s+(?:want|need)\s+(?:a\s+|the\s+)?)$",
            prefix,
            re.I,
        ) or re.search(
            r"\b(?:not\s+(?:(?:applying|going to apply)\s+for\s+)?(?:a\s+|the\s+)?|"
            r"(?:don['’]t|do not)\s+(?:want|need)\s+(?:a\s+|the\s+)?)"
            r"[^.;!?]{0,35}(?:visa|route)\s*$",
            window,
            re.I,
        ):
            continue
        return True
    return False
