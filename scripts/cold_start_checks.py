"""Frozen state checks and lexical information proxies, never a naturalness score.

Only the selected split is expanded. Expectations never become model input. These checks
must be supplemented by reading the complete conversation: keyword presence cannot prove
that an explanation is accurate, relevant, exhaustive, or pleasant to read.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from visa_agent.domain.locations import location_key
from visa_agent.domain.models import Case

PROFILE_FIELDS = frozenset({
    "full_name", "date_of_birth", "nationality_country", "application_country", "visit_purpose",
    "occupation_status", "funding_source", "estimated_trip_cost_gbp", "planned_arrival_date",
    "planned_departure_date",
})
DATE_FIELDS = frozenset({"date_of_birth", "planned_arrival_date", "planned_departure_date"})
TRAVEL_FIELDS = {"planned_arrival_date", "planned_departure_date"}
INFORMATION_TAGS = frozenset({
    "official_application_entry", "document_purpose", "translation_requirements",
    "bank_evidence_purpose", "corrected_fact_acknowledged", "no_unsolicited_document_request",
    "resume_acknowledged", "next_step_action",
})
TURN_KEYS = frozenset({
    "id", "body", "expected_profile", "deferred_date_expected", "preparation_paused_expected",
    "expected_information", "forbidden_reasked_fields", "rationale",
})
JOURNEY_KEYS = frozenset({"id", "split", "language", "subject", "turns"})
APPLICATION_URLS = (
    "https://www.gov.uk/standard-visitor/apply-standard-visitor-visa",
    "https://visas-immigration.service.gov.uk/apply-visa-type/visit",
)
EXPECTED_ALIASES = {
    "funding_source": {"self-funded": "self"},
    "occupation_status": {"self-employed": "self_employed"},
}


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: object, allowed: frozenset[str]) -> bool:
    return (isinstance(value, list) and all(isinstance(item, str) and item in allowed for item in value)
            and len(value) == len(set(value)))


def load_journeys(corpus_bytes: bytes, split: str) -> list[dict[str, Any]]:
    if split not in {"development", "holdout"}:
        raise ValueError("Choose one supported split")
    corpus = json.loads(corpus_bytes)
    if (not isinstance(corpus, list) or not corpus
            or any(not isinstance(item, dict) or item.get("split") not in {"development", "holdout"}
                   for item in corpus)):
        raise ValueError("Corpus must contain split-labelled journeys")
    selected = [item for item in corpus if item["split"] == split]
    if not selected or len(selected) > 2:
        raise ValueError("Each selected experiment permits one or two journeys")
    ids: set[str] = set()
    for journey in selected:
        if (set(journey) != JOURNEY_KEYS or not _text(journey["id"])
                or journey["id"] in ids or journey["language"] not in {"zh", "en"}
                or not _text(journey["subject"]) or "\n" in journey["subject"] or "\r" in journey["subject"]
                or not isinstance(journey["turns"], list) or len(journey["turns"]) != 6):
            raise ValueError("Selected journey has invalid or extra fields")
        ids.add(journey["id"])
        turn_ids: set[str] = set()
        previous_fields: set[str] = set()
        for turn in journey["turns"]:
            if (not isinstance(turn, dict) or set(turn) != TURN_KEYS
                    or not all(_text(turn[name]) for name in ("id", "body", "rationale"))
                    or turn["id"] in turn_ids
                    or type(turn["deferred_date_expected"]) is not bool
                    or type(turn["preparation_paused_expected"]) is not bool
                    or not _string_list(turn["expected_information"], INFORMATION_TAGS)
                    or not _string_list(turn["forbidden_reasked_fields"], PROFILE_FIELDS)
                    or not isinstance(turn["expected_profile"], dict)
                    or not set(turn["expected_profile"]) <= PROFILE_FIELDS):
                raise ValueError("Selected turn has invalid or extra fields")
            turn_ids.add(turn["id"])
            profile = turn["expected_profile"]
            if not previous_fields <= set(profile):
                raise ValueError("Expected profiles must be cumulative, not silently drop earlier fields")
            previous_fields = set(profile)
            for field, value in profile.items():
                if value is None:
                    continue
                if field == "estimated_trip_cost_gbp":
                    if type(value) is not int or value < 0:
                        raise ValueError("Budget expectation must be a nonnegative integer")
                elif not _text(value):
                    raise ValueError("Fact expectations must be strings or null")
                elif field in DATE_FIELDS:
                    try:
                        if date.fromisoformat(value).isoformat() != value:
                            raise ValueError
                    except ValueError as error:
                        raise ValueError("Date expectations must use exact ISO calendar dates") from error
            if turn["deferred_date_expected"] and any(profile.get(field) is not None for field in TRAVEL_FIELDS):
                raise ValueError("Deferred-date expectations cannot also supply exact travel dates")
    return selected


def _has(body: str, pattern: str) -> bool:
    return re.search(pattern, body, re.I) is not None


# These patterns identify requests, not mentions in an acknowledgement or summary.
QUESTION_PATTERNS = {
    "date_of_birth": r"出生日期(?:是什么|是几|是哪)|(?:what|when).{0,30}(?:date of birth|were you born)|请.{0,12}(?:生日|出生日期)",
    "full_name": r"(?:告诉|提供).{0,12}(?:姓名|名字)|what is your name|(?:please|could you).{0,15}(?:provide|tell).{0,15}name",
    "nationality_country": r"哪[个一]?(?:国家|国).{0,8}护照|which country.{0,24}passport",
    "application_country": r"哪[个一]?(?:国家|地区).{0,12}(?:递交|申请)|which country.{0,25}(?:apply|applying)",
    "visit_purpose": r"这次.{0,10}(?:旅游|探亲).{0,15}还是|what is the (?:main )?(?:reason|purpose)",
    "occupation_status": r"目前.{0,8}(?:工作|读书).{0,15}还是|are you currently.{0,15}(?:employed|studying)",
    "funding_source": r"(?:费用|旅行).{0,15}还是.{0,12}资助|who will pay|who is paying",
    "estimated_trip_cost_gbp": r"(?:打算花|预算是|大概花|预计花).{0,8}多少|how much.{0,25}(?:cost|spend|budget)",
    "planned_arrival_date": r"(?:计划|准备|打算)?哪天.{0,8}(?:到|抵达|抵英)|(?:when|what dates?).{0,30}(?:arriv|travel|visit)",
    "planned_departure_date": r"(?:计划|准备|打算)?哪天.{0,8}(?:离开|回国)|(?:when|what dates?).{0,30}(?:leav|depart|return)",
}


def unsolicited_document_request(body: str) -> bool:
    """A conservative lexical alarm; not proof of understanding every negation."""
    for clause in re.split(r"[。！？!?；;\n]|\.(?:\s|$)|[,，]?\s*(?:但(?:是)?|不过|\bbut\b)\s*", body, flags=re.I):
        if _has(clause, r"(?:无需|不用|不必|不是要求|不要求|不需要)|"
                       r"\b(?:not a request|no need|do not need|don't need|needn't)\b|"
                       r"(?:如果|等你).{0,20}(?:之后|以后|继续|恢复)|\bif.{0,30}(?:later|resume|restart)\b"):
            continue
        if not _has(clause, r"护照|材料|文件|证明|流水|对账单|译文|附件|扫描|复印|照片|"
                           r"\b(?:passport|documents?|evidence|statements?|letters?|translations?|attachments?|scans?|copies|photos?)\b"):
            continue
        if _has(clause, r"(?:请|麻烦|需要你|先把).{0,25}(?:发来|提供|补交|上传|发给|准备)|"
                       r"(?:回复.{0,15}附上|接下来还需要这些材料|we['’]ll also need these documents)|"
                       r"\b(?:please (?:send|attach|upload|provide)|send me|email me|attach (?:your|the))\b"):
            return True
    return False


def information_proxies(tag: str, case: Case, body: str) -> bool:
    """Weak content-presence proxies kept separate from human semantic judgement."""
    if not body.strip():
        return False
    if tag == "official_application_entry":
        return any(url in body for url in APPLICATION_URLS)
    if tag == "document_purpose":
        return (_has(body, r"护照|证明|流水|资金|passport|evidence|statement|letter")
                and _has(body, r"核对|用于|用来|说明|支持|证明.{1,20}(?:身份|学习|工作|资金)|"
                                   r"\b(?:to (?:show|support|check|confirm|explain)|evidence of|confirming|demonstrat)"))
    if tag == "bank_evidence_purpose":
        return (_has(body, r"流水|银行|对账单|bank|statement")
                and _has(body, r"来源|哪里来|source|come from")
                and _has(body, r"能否使用|是否能使用|可用|能使用|支配|access|available"))
    if tag == "translation_requirements":
        return all(_has(body, pattern) for pattern in (
            r"翻译|译文|translation", r"完整|full|complete", r"准确|accuracy|accurate",
            r"姓名|全名|name", r"签名|signature|signed", r"日期|date", r"联系|contact",
        ))
    if tag == "corrected_fact_acknowledged":
        return bool(case.latest_changes) and _has(body, r"更正|改为|改成|更新|已改|updated|correct|changed|change to")
    if tag == "no_unsolicited_document_request":
        return not unsolicited_document_request(body)
    if tag == "resume_acknowledged":
        return (case.latest_preparation_action == "resume" and not case.preparation_paused
                and _has(body, r"继续|恢复|pick this up|resume|continue"))
    if tag == "next_step_action":
        advice = case.next_step_advice
        return bool(advice and advice.kind in {"question", "document"} and advice.message in body)
    raise ValueError("Unknown information obligation")


def check_turn(journey: dict[str, Any], turn: dict[str, Any], case: Case, body: str) -> dict[str, bool]:
    observed = case.profile.model_dump(mode="json")
    expected = turn["expected_profile"]
    checks = {
        "body_not_empty": bool(body.strip()),
        "language": case.customer_language == journey["language"],
        "pause_state": case.preparation_paused == turn["preparation_paused_expected"],
        "dates_deferred": (set(case.deferred_fields) & TRAVEL_FIELDS)
        == (TRAVEL_FIELDS if turn["deferred_date_expected"] else set()),
        "no_unrequested_consent_or_pack": not case.profile_confirmed and not case.final_summary_confirmed and case.delivery_path is None,
        "routine_not_escalated": case.status.value != "HUMAN_REVIEW_REQUIRED",
    }
    for field in sorted(PROFILE_FIELDS):
        actual, wanted = observed.get(field), expected.get(field)
        wanted = EXPECTED_ALIASES.get(field, {}).get(wanted, wanted)
        if field in {"nationality_country", "application_country"} and actual is not None and wanted is not None:
            checks[f"profile:{field}"] = location_key(actual) == location_key(wanted)
        else:
            checks[f"profile:{field}"] = actual == wanted and type(actual) is type(wanted)
    forbidden = set(turn["forbidden_reasked_fields"]) | {field for field, value in expected.items() if value is not None}
    if turn["deferred_date_expected"]:
        forbidden |= TRAVEL_FIELDS
    checks["known_or_deferred_fields_not_planned"] = not forbidden.intersection(case.last_requested_fields)
    for field in sorted(forbidden):
        checks[f"no_reask_text_proxy:{field}"] = not _has(body, QUESTION_PATTERNS[field])
    for tag in turn["expected_information"]:
        checks[f"information_proxy:{tag}"] = information_proxies(tag, case, body)
    return checks
