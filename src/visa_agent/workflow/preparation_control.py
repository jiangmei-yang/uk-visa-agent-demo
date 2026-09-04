"""Conservative evidence guard for a proposed preparation preference.

The model interprets the email; this module does not attempt to replace it with an
exhaustive language parser. It accepts a narrow, explicit current request grounded
in the complete containing clause. Unsupported wording is left unchanged, never
guessed into authority. No profile, consent, delivery or review state is changed.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal

from visa_agent.llm.ports import PreparationIntent
from visa_agent.workflow.conversation import latest_reply_text

_SCOPE = (
    r"(?:\b(?:visa(?:\s+application)?|application|preparations?|paperwork|"
    r"supporting\s+documents|document\s+preparation)\b|签证(?:申请)?|申请|材料|准备|办理)"
)
_PAUSE = (
    r"(?:\b(?:pause|stop|suspend|postpone|hold\s+off\s+on|take\s+a\s+break\s+from|"
    r"set\s+aside|put\s+aside|continue\s+holding)\b|暂停|暂缓|缓一缓|停一下|先停|搁置|"
    r"(?:先|暂时|暂且)?(?:全部|整体|都)?放一放|先放下)"
)
_RESUME = (
    r"(?:\b(?:resume|restart|continue|carry\s+on\s+with|get\s+back\s+to|"
    r"go\s+ahead\s+with|move\s+forward\s+with)\b|继续|恢复|重新开始|接着)"
)
_BETWEEN = r"[^。.!?？；;\n，,]{0,64}?"
_TAKE_OFF_HOLD = (
    r"\btake\s+(?:(?:my|our|the|this|all|whole|entire)\s+){0,3}"
    rf"(?:(?:UK|visitor)\s+){{0,2}}{_SCOPE}"
    r"(?:\s+(?:preparation|paperwork|documents))?\s+off\s+hold\b"
)
_NEGATED_CONTINUATION = (
    rf"(?:\b(?:do\s+not|don't|don’t|not\s+(?:ready|able)|no\s+longer\s+want)"
    rf"\s+(?:(?:want|wish|plan)\s+to\s+|to\s+)?{_RESUME}{_BETWEEN}{_SCOPE}|"
    rf"(?:先不|暂不|暂时不|目前不|不想|不打算|不要|不用){_BETWEEN}"
    rf"(?:继续|推进|准备|办|申请){_BETWEEN}{_SCOPE}|"
    rf"{_SCOPE}{_BETWEEN}(?:先不|暂不|暂时不|目前不|不想|不打算|不要|不用)"
    rf"{_BETWEEN}(?:继续|推进|准备|办))"
)
_PATTERNS: dict[Literal["pause", "resume"], re.Pattern[str]] = {
    "pause": re.compile(
        rf"{_NEGATED_CONTINUATION}|{_PAUSE}{_BETWEEN}{_SCOPE}|"
        rf"{_SCOPE}{_BETWEEN}{_PAUSE}|"
        rf"\bput\b{_BETWEEN}{_SCOPE}{_BETWEEN}\bon\s+hold\b|"
        rf"{_SCOPE}{_BETWEEN}(?:先不办了|先不做了|先放一放|on\s+hold)", re.I,
    ),
    "resume": re.compile(
        rf"{_RESUME}{_BETWEEN}{_SCOPE}|{_SCOPE}{_BETWEEN}{_RESUME}|"
        rf"\bpick\b{_BETWEEN}{_SCOPE}{_BETWEEN}\b(?:back\s+)?up\b|"
        rf"{_TAKE_OFF_HOLD}", re.I,
    ),
}
_INTERNAL_COMMAND = re.compile(
    r"preparation_intent|preparation_paused|source_excerpt|requires_human_review|"
    r"(?:set|output|return|assign|write).{0,30}(?:json|schema|variable|field|state)|"
    r"(?:ignore|bypass|override).{0,30}(?:rules?|checks?|instructions?|system)|"
    r"(?:设置|修改|输出|返回).{0,12}(?:变量|字段|状态|JSON)|"
    r"(?:忽略|跳过|绕过).{0,12}(?:指令|规则|检查|系统)", re.I,
)
_THIRD_PARTY_OR_HISTORY = re.compile(
    r"\b(?:he|she|they|my\s+(?:friend|sister|brother|mother|father|partner|client)|"
    r"the\s+(?:client|customer|applicant))\b.{0,40}"
    r"\b(?:want|wants|asked|asks|said|says|plans?|needs?)\b|"
    r"\b(?:said|wrote|asked|requested|used\s+to|previously|earlier|yesterday|last\s+time)\b|"
    r"(?:她|他|他们|她们|朋友|同学|姐姐|妹妹|哥哥|弟弟|妈妈|爸爸|客户).{0,12}(?:说|想|打算|要求)|"
    r"(?:之前|以前|上次|昨天|原来|曾经).{0,18}(?:说|写|要求|希望|打算|暂停|继续)", re.I,
)
_NONCURRENT = re.compile(
    r"\b(?:if|unless|when|once|until|after|whether|tomorrow|later|eventually|next\s+week|"
    r"might|may|perhaps|possibly)\b|"
    r"(?:如果|假如|假设|若是|到时候|之后再|以后再|稍后再|明天|下周|等.{0,24}再|可能|也许)", re.I,
)
_INFORMATION_REQUEST = re.compile(
    r"\b(?:what\s+(?:if|happens)|how\s+(?:do|can|would|to)|explain(?:ing)?|explanation|meaning|means?|"
    r"definition|translate|translation|example|rephrase|spell)\b|"
    r"(?:解释|是什么意思|什么后果|会怎么样|如何|怎样|怎么|翻译|举例|示例|拼写)", re.I,
)
_SINGLE_ITEM = re.compile(
    r"\bpreparation\s+of\s+(?:(?:my|the|a|an|one)\s+)?"
    r"(?:employment\s+letter|bank\s+statement|passport|translation|itinerary)\b|"
    r"\b(?:only|just)\b.{0,30}\b(?:document|letter|statement|passport|translation)\b|"
    r"\b(?:document|letter|statement|passport|translation)\b.{0,15}\bonly\b|"
    r"(?:这份|那份|这一份|那一份|单份|单项).{0,8}(?:材料|准备)|"
    r"(?:银行流水|护照(?:照片|扫描)?|在职证明|翻译件).{0,8}(?:材料|准备|整理)", re.I,
)
_OTHER_APPLICATION = re.compile(
    r"\b(?:mortgage|loan|job|university|college|software|housing)\s+application\b|"
    r"(?:大学|学校|工作|贷款|住房|职位)申请", re.I,
)
_CHANGE_OF_MIND = re.compile(
    r"\b(?:actually|instead|on\s+second\s+thought|changed\s+my\s+mind|"
    r"but\s+now|however\s+now|rather)\b|"
    r"(?:改主意|改变主意|还是|算了|不对|不过现在|但现在|改为)", re.I,
)
_NEGATION = re.compile(
    r"\b(?:not|never|don't|don’t|do\s+not|no\s+need\s+to|wouldn't|wouldn’t)\b|"
    r"(?:不要|不用|无需|不必|不想|不打算|不能|并非|不是|没有|没说|别)", re.I,
)
_QUOTES = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|(?<!\w)\x27[^\x27\n]+\x27(?!\w)|`[^`\n]+`')
_MAINTAINED_CONTINUATION = re.compile(
    r"\bcontinue\b(?=\s+(?:to\s+)?(?:(?:be|remain)\s+)?"
    r"(?:(?:the|this|current)\s+)?(?:pause\b|holding\b|on\s+hold\b))|"
    r"(?:继续|接着)(?=(?:整体|全部|全面|暂时|先|保持|维持){0,3}(?:暂停|暂缓|搁置))",
    re.I,
)
_PAST_PAUSE_MODIFIER = re.compile(
    # These clauses describe the preparation being resumed, not a second current
    # instruction. Keep the scope noun and all surrounding authority qualifiers.
    rf"(?P<scope>{_SCOPE})(?P<relative>\s+(?:(?:that|which)\s+)?(?:I|we)\s+"
    r"(?:had\s+)?(?:(?:previously|earlier)\s+)?"
    r"(?:(?:put|placed|set)\s+on\s+hold|paused|suspended|postponed))\b|"
    r"(?P<adjective>\b(?:previously|earlier)\s+(?:paused|suspended|postponed)\s+)"
    rf"(?=(?:(?:UK|visitor)\s+){{0,2}}{_SCOPE})|"
    r"(?P<zh>(?:之前|以前|此前|先前|上次)(?:暂时|整体|全部)?"
    r"(?:暂停|暂缓|搁置)(?:过|了)?的)(?=[^。.!?？；;\n，,]{0,16}"
    rf"{_SCOPE})",
    re.I,
)


def _mask_past_pause_modifiers(text: str) -> str:
    """Mask only attributive history while preserving exact evidence offsets."""
    def mask(match: re.Match[str]) -> str:
        scope = match.group("scope") or ""
        return scope + " " * (len(match[0]) - len(scope))

    return _PAST_PAUSE_MODIFIER.sub(mask, text)


@dataclass(frozen=True)
class _Control:
    action: Literal["pause", "resume"]
    start: int
    end: int
    changes_mind: bool


def _clauses(sentence: str) -> Iterator[tuple[int, str]]:
    """Keep exact offsets while separating a new coordinated control request."""
    start = 0
    separators = r"[，,]|\band\s+(?=(?:when|once|if|until|resume|pause|continue|please)\b)"
    for separator in re.finditer(separators, sentence, re.I):
        yield start, sentence[start:separator.start()]
        start = separator.end()
    yield start, sentence[start:]


def _current_controls(text: str) -> list[_Control]:
    controls: list[_Control] = []
    # Keep offsets: replacing quoted text cannot manufacture new adjacent words.
    unquoted = _mask_past_pause_modifiers(_QUOTES.sub(lambda match: " " * len(match[0]), text))
    for sentence in re.finditer(r"[^。.!?？；;\n]+", unquoted):
        previous_context = ""
        pending_change = False
        for clause_offset, raw in _clauses(sentence[0]):
            context = previous_context + raw
            changed = bool(_CHANGE_OF_MIND.search(raw))
            if changed:
                # An explicit CURRENT reversal starts fresh; temporal language in
                # the new request still blocks it below.
                context = raw
                pending_change = True
            previous_context = context + ","
            if (_INTERNAL_COMMAND.search(context) or _THIRD_PARTY_OR_HISTORY.search(context)
                    or _INFORMATION_REQUEST.search(raw) or _SINGLE_ITEM.search(raw)
                    or _OTHER_APPLICATION.search(raw)):
                continue
            pause = _PATTERNS["pause"].search(raw)
            # 'Continue the pause' maintains a pause, rather than resuming work.
            # Mask only that governing verb, preserving offsets and any separate
            # real resume request; negating it cannot become a new pause either.
            resume_text = _MAINTAINED_CONTINUATION.sub(lambda item: " " * len(item[0]), raw)
            resume = _PATTERNS["resume"].search(resume_text)
            if re.search(_TAKE_OFF_HOLD, resume_text, re.I) and re.search(
                r"\bfor\s+(?:her|him|them|my\s+(?:friend|sister|brother|mother|father|partner|client))\b",
                raw, re.I,
            ):
                resume = None  # A direct request about someone else's application is not this case's preference.
            negated_continuation = bool(re.search(_NEGATED_CONTINUATION, resume_text, re.I))
            if negated_continuation:
                resume = None
            candidates: dict[Literal["pause", "resume"], re.Match[str] | None] = {
                "pause": pause, "resume": resume,
            }
            for action, match in candidates.items():
                if match is None:
                    continue
                temporal_context = context
                until = re.search(r"\buntil\b", raw, re.I)
                if action == "pause" and until is not None and match.end() <= until.start():
                    # 'Pause until I return' starts a pause now; 'until I return,
                    # resume' is still future-only and never reaches this branch.
                    temporal_context = re.sub(r"\buntil\b.*$", "", context, flags=re.I)
                if _NONCURRENT.search(temporal_context):
                    continue
                if _NEGATION.search(raw) and not (action == "pause" and negated_continuation):
                    continue
                offset = sentence.start() + clause_offset
                controls.append(_Control(action, offset + match.start(), offset + match.end(), pending_change))
                pending_change = False
    return controls


def validated_preparation_intent(
    body: str, proposed: PreparationIntent | None,
) -> PreparationIntent | None:
    """Validate a typed proposal without inventing a missing one or changing state.

    The exact excerpt must support the resolved current action. Conflicting current
    directions require an explicit later change of mind; mere last mention does not
    win. False negatives can occur for unfamiliar wording: this conservative gate
    is not a claim of universal intent accuracy.
    """
    if proposed is None or proposed.confidence < 0.8 or not proposed.source_excerpt.strip():
        return None
    current = latest_reply_text(body)
    excerpt = proposed.source_excerpt
    if excerpt not in body or excerpt not in current:
        return None
    unquoted = _QUOTES.sub(lambda match: " " * len(match[0]), current)
    supported_positions = [(match.start(), match.end())
                           for match in re.finditer(re.escape(excerpt), unquoted)]
    if not supported_positions:
        return None
    controls = _current_controls(current)
    if not controls:
        return None
    selected = controls[0]
    supported_controls = [selected]
    conflict = False
    for control in controls[1:]:
        if control.action != selected.action:
            if control.changes_mind:
                selected, conflict = control, False
                supported_controls = [control]
            else:
                conflict = True
        elif control.changes_mind:
            selected, conflict = control, False
            supported_controls = [control]
        elif not conflict:
            supported_controls.append(control)
    if conflict or selected.action != proposed.action:
        return None
    if not any(start <= control.start and control.end <= end
               for start, end in supported_positions for control in supported_controls):
        return None
    return proposed
