"""Frozen intent corpus -> real extraction -> guarded workflow -> captured Gmail only.

This measures bounded topic classification and selected safety/content invariants,
not naturalness, comprehensive legal accuracy, real Gmail delivery, or pack quality.
The holdout split is opt-in and must not be used to tune prompts or matching rules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import Case, CaseProfile, InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import deterministic_fallback_message, validate_case_patch
from visa_agent.llm.ports import CasePatch
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL
from visa_agent.workflow.conversation import (
    QUESTION_TEXT_EN,
    QUESTION_TEXT_ZH,
    latest_reply_text,
    next_fact_questions,
    reply_items,
)
from visa_agent.workflow.service import WorkflowService

REPOSITORY = Path(__file__).resolve().parents[1]
CORPUS = REPOSITORY / "evals/adviser_intent_cases.json"
POLICY = REPOSITORY / "knowledge/uk_standard_visitor_2026-02-25.yaml"
MODEL = "deepseek-v4-flash"
REVIEWED_AS_OF = date(2026, 9, 4)
SOURCE_FILES = (
    "src/visa_agent/domain/models.py",
    "src/visa_agent/llm/ports.py",
    "src/visa_agent/llm/openai_client.py",
    "src/visa_agent/llm/deepseek_client.py",
    "src/visa_agent/llm/guarded.py",
    "src/visa_agent/workflow/customer_questions.py",
    "src/visa_agent/workflow/adviser_guidance.py",
    "src/visa_agent/workflow/service.py",
    "src/visa_agent/workflow/conversation.py",
    "src/visa_agent/channels/automatic_reply.py",
    "scripts/adviser_intent_probe.py",
)


class CapturedPatchModel:
    """Reuse exactly one paid extraction; generation is the reviewed renderer."""

    version = "frozen-real-deepseek-patch-no-second-api-call"

    def __init__(self, proposed: CasePatch) -> None:
        self.proposed = proposed
        self.extract_calls = 0

    def extract_case_patch(self, event: InboundEvent) -> CasePatch:
        self.extract_calls += 1
        return self.proposed.model_copy(deep=True)

    render_message = staticmethod(deterministic_fallback_message)


class CaptureGmail(GmailAdapter):
    """Do not initialise a Gmail client, credentials, or a provider connection."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"id": f"intent-capture-{len(self.calls)}"}


def source_fingerprints() -> dict[str, str]:
    return {
        name: hashlib.sha256((REPOSITORY / name).read_bytes()).hexdigest()
        for name in SOURCE_FILES
    }


def seed_case(item: dict[str, Any], policy_version: str) -> Case:
    """Known fictional intake context, not evidence of document validity or consent."""
    profile: dict[str, Any] = {
        "full_name": "Example Applicant",
        "date_of_birth": "1998-05-12",
        "nationality": "Chinese",
        "nationality_country": "China",
        "application_country": "Hong Kong",
        "planned_arrival_date": None,
        "planned_departure_date": None,
        "visit_purpose": "tourism",
        "uk_accommodation": "Planned stay in London; no booking made",
        "estimated_trip_cost_gbp": 1500,
        "current_address": "Fictional campus address, Hong Kong",
        "occupation_status": "student",
        "annual_income_gbp": 0,
        "funding_source": "self",
        "has_serious_history": False,
        "route_confirmed_standard_visitor": True,
    }
    profile.update(item.get("context_profile", {}))
    return Case(
        id=f"probe-{item['id']}",
        external_thread_id=f"probe-thread-{item['id']}",
        applicant_contact="fictional-intent@example.test",
        primary_channel="gmail",
        policy_version=policy_version,
        customer_language=item["language"],
        profile=CaseProfile.model_validate(profile),
        deferred_fields=["planned_arrival_date", "planned_departure_date"],
    )


def topic_metrics(expected: list[str], actual: list[str]) -> dict[str, Any]:
    wanted, got = set(expected), set(actual)
    true_positive = len(wanted & got)
    return {
        "exact": wanted == got,
        "true_positive": true_positive,
        "false_positive": sorted(got - wanted),
        "false_negative": sorted(wanted - got),
        "precision": true_positive / len(got) if got else (1.0 if not wanted else 0.0),
        "recall": true_positive / len(wanted) if wanted else 1.0,
    }


def current_evaluation_clauses(body: str, *, split_commas: bool = True) -> list[str]:
    """Independent evaluator view: quoted wording is not a new customer request."""
    text = latest_reply_text(body)
    text = re.sub(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"', "", text)
    text = re.sub(r"(?<!\w)'[^'\n]+'(?!\w)|`[^`\n]+`", "", text)
    separators = r"[。！!；;\n，,]" if split_commas else r"[。！!；;\n]"
    return [clause.strip() for clause in re.split(
        separators + r"|(?<=[?？])\s*|\.(?:\s|$)", text,
    ) if clause.strip()]


def explicit_preparation_request(body: str) -> bool:
    """Allow requested preparation, not quoted, declined or hypothetical continuation.

    This does not grant confirmation, submission or release authority. It only
    prevents the evaluator treating requested guidance as unsolicited guidance.
    """
    for clause in current_evaluation_clauses(body, split_commas=False):
        if re.search(
            r"不要|别|先不|暂时不|不想|不需要|不准备|不能|无需|不用|还没|尚未|如果|假如|"
            r"\b(?:not|never|stop|no need|if|maybe|later|tomorrow|"
            r"don['’]t|can['’]t|cannot|wouldn['’]t|couldn['’]t|shouldn['’]t|won['’]t)\b",
            clause, re.I,
        ):
            continue
        subject = re.search(r"签证|申请|材料|资料|\b(?:visa|application|documents?|evidence|preparation)\b", clause, re.I)
        affirmative = re.search(
            r"(?:请|麻烦|我们|咱们|我想|我准备|现在可以|现在|可以)?(?:继续|开始|恢复|接着|推进)|"
            r"\b(?:let['’]s|please|could (?:we|you)|can (?:we|you)|"
            r"(?:i am|i['’]m|we are|we['’]re) ready to|i (?:want|would like) to)\s+"
            r"(?:continue|carry on|proceed|resume|start|begin|prepare|organise|organize)\b",
            clause, re.I,
        )
        if subject and affirmative:
            return True
    return False


def bank_acquisition_requested(body: str) -> bool:
    clauses = current_evaluation_clauses(body)
    if not re.search(r"银行|流水|对账单|\bbank|\bstatements?\b", " ".join(clauses), re.I):
        return False
    for clause in clauses:
        if re.search(
            r"(?:不用|无需|不要|不想|不需要).{0,15}(?:解释|告诉|说明|回答)|不问|"
            r"\b(?:not asking|not interested|don['’]t explain|do not explain|no need to explain)\b",
            clause, re.I,
        ):
            continue
        if re.search(
            r"网银|网上银行|手机银行|下载|电子(?:版|账单)|获取|开具|无纸化|"
            r"哪里.{0,8}(?:拿|找|开)|\b(?:online|download|paperless|e-statements?)\b|"
            r"\b(?:where|how).{0,45}(?:get|obtain|request)\b|\bpaper copies\b",
            clause, re.I,
        ):
            return True
    return False


def bank_acceptance_guaranteed(body: str) -> bool:
    """Detect bounded affirmative promises without treating any earlier 'not' as scope.

    A directly negated promise, or its 'that ...' complement, is not affirmative.
    Sentence/clause boundaries and a later independent assertion end that scope;
    negation is not limited to an arbitrary character-distance window.
    """
    guarantee = re.compile(
        r"guaranteed (?:to be accepted|acceptance)|will (?:always )?be accepted|"
        r"automatically accepted|always acceptable|guarantees? (?:acceptance|approval)|"
        r"一定(?:会)?(?:被接受|获批|符合|满足)|保证(?:被接受|获批|符合|满足)|自动(?:被)?接受",
        re.I,
    )
    direct_negative = re.compile(
        r"(?:\b(?:not|never|no|cannot|can['’]t|doesn['’]t|don['’]t)\b|不能|无法|不|未)"
        r"\s*(?:an?\s+|any\s+)?$", re.I,
    )
    negative_governor = re.compile(
        r"\b(?:not\s+(?:(?:a|an|any)\s+)?|no\s+)(?:guarantee|promise|confirmation)\s+that\b|"
        r"\b(?:cannot|can['’]t|doesn['’]t|don['’]t|does not|do not|could not|never)\s+"
        r"(?:guarantee|promise|mean|imply|ensure|say)\s+that\b",
        re.I,
    )
    independent_assertion = (
        r"\b(?:but|however|nevertheless|yet|instead)\b|但是|不过|然而|可是|但|"
        r"\band\s+(?=(?:i|we)\s+(?:guarantee|promise)\b)|"
        r"\band\s+(?=(?:these|those|your|the)\s+(?:files|copies|documents|statements)\s+will\b)"
    )
    for sentence_clause in current_evaluation_clauses(body):
        for clause in re.split(independent_assertion, sentence_clause, flags=re.I):
            for match in guarantee.finditer(clause):
                prefix = clause[:match.start()]
                if direct_negative.search(prefix) or negative_governor.search(prefix):
                    continue
                return True
    return False


def content_checks(
    expected: list[str], case: Case, body: str, language: str,
    *, incoming_body: str = "", unchanged_profile: bool = False,
    development_checks: bool = False,
) -> dict[str, bool]:
    """Small reviewed-source checks, not an LLM judge or a style/naturalness score."""
    checks: dict[str, bool] = {}
    zh = language == "zh"
    expected_set = set(expected)
    questions, _, documents = reply_items(case)
    default_document_block = bool(re.search(
        r"接下来还需要这些材料|We'll also need these documents", body, re.I,
    )) or any(re.search(rf"(?m)^\s*[-*]\s*{re.escape(document)}", body)
              for document in documents)
    for topic in expected:
        if topic in {"application", "timing", "fees"}:
            checks[f"{topic}_official_source"] = APPLICATION_URL in body
        elif topic in {"translation", "booking", "bank_period"}:
            checks[f"{topic}_official_source"] = DOCUMENTS_URL in body
        if topic == "application":
            checks["application_actionable_entry"] = "Apply now" in body
        elif topic == "timing":
            checks["timing_window_and_decision_distinct"] = (
                "3 个月" in body and "3 周" in body and "不保证" in body
                if zh else "3 months" in body and "3 weeks" in body and "not a guaranteed" in body
            )
        elif topic == "translation":
            checks["translation_verification_details"] = (
                "完整翻译" in body and "签名" in body and "联系方式" in body
                if zh else "full translation" in body and "signature" in body and "contact details" in body
            )
        elif topic == "booking":
            checks["booking_not_required_as_evidence"] = (
                "不需要" in body and "预订" in body
                if zh else "do not need to buy flights" in body
            )
        elif topic == "fees":
            checks["fee_limited_to_six_month_route"] = (
                "£135" in body and "Standard Visitor" in body
                and ("6 个月" in body if zh else "6-month" in body)
            )
        elif topic == "bank_period":
            # The historical topic also includes access/collection questions. A
            # monthly-period boilerplate is not required when no period was asked.
            period_question = not incoming_body or bool(re.search(
                r"个月|多久|多长|哪几|月份|追溯|跨度|\bmonths?\b|\byears?\b|"
                r"\bperiod\b|how.{0,20}back",
                "\n".join(current_evaluation_clauses(incoming_body)), re.I,
            ))
            checks["bank_period_not_invented_fixed_rule"] = (
                ("资金来源" in body if zh else "funds come from" in body)
                and (not period_question or (
                    "没有统一规定" in body if zh else "does not set one fixed number of months" in body
                ))
            )
            if not period_question:
                checks["funds_answer_does_not_impose_fixed_statement_period"] = not re.search(
                    r"(?:必须|至少|需要).{0,8}[0-9一二三四六十]+.{0,3}(?:个月|月流水)|"
                    r"\b(?:must|need|required|at least).{0,30}\b\d+.{0,5}months?\b", body, re.I,
                )
            if not {"application", "timing"}.intersection(expected_set):
                checks["bank_period_no_unrequested_visa_timing"] = not re.search(
                    r"(?:出发|旅行)前.{0,16}(?:个月|周|天)|"
                    r"(?:决定|出结果|审理).{0,35}(?:个月|周|天)|"
                    r"(?:个月|周|天).{0,35}(?:决定|出结果|审理)|"
                    r"\b(?:apply|application).{0,80}(?:months?|weeks?|days?).{0,25}(?:before|ahead)|"
                    r"\b(?:decision|processing time).{0,65}(?:days?|weeks?|months?)",
                    body, re.I,
                )
            if bank_acquisition_requested(incoming_body):
                checks["bank_acquisition_practical_next_step"] = bool(re.search(
                    r"(?:网银|网上银行|手机银行|银行.{0,6}APP).{0,35}(?:下载|导出|获取|申请)|"
                    r"(?:向|联系|询问|请).{0,8}银行.{0,30}(?:申请|索取|开具|提供|获取)|"
                    r"(?:索取|申请|联系|询问).{0,12}银行|"
                    r"(?:bank(?:['’]s)?\s+(?:website|portal|app)|online banking|banking app)"
                    r".{0,60}(?:download|export|statements?)|"
                    r"(?:download|export).{0,65}(?:bank(?:['’]s)?\s+(?:website|portal|app)|online banking)|"
                    r"\b(?:request|ask|contact).{0,35}\bbank\b",
                    body, re.I,
                ))
                checks["bank_acquisition_no_acceptance_guarantee"] = not bank_acceptance_guaranteed(body)
        elif topic == "document_checklist":
            documents = reply_items(case)[2]
            checks["checklist_at_least_three_relevant_items"] = (
                len(documents) >= 3 and all(document in body for document in documents)
            )
        elif topic == "unsupported":
            checks["unsupported_acknowledged_as_unverified"] = (
                "核" in body and ("不能" in body or "需要" in body)
                if zh else "check" in body.lower()
                and any(word in body.lower() for word in ("verified", "reliably", "human adviser"))
            )
            if not {"fees", "timing"} & set(expected):
                checks["unsupported_not_answered_with_narrow_fee_or_timing"] = (
                    "£135" not in body and not re.search(r"3\s*(?:weeks?|周)", body, re.I)
                )
        elif topic == "off_topic":
            scope_paragraphs = [paragraph for paragraph in body.split("\n\n") if (
                re.search(r"英国签证|UK visa|British visa", paragraph, re.I)
                and re.search(
                    r"不属于|超出|范围之外|不在.{0,16}范围|outside|beyond|out of scope|not within",
                    paragraph, re.I,
                )
            )]
            checks["off_topic_scope_boundary"] = bool(scope_paragraphs)
            checks["off_topic_scope_note_brief"] = bool(scope_paragraphs) and all(
                len(paragraph) <= 240 if zh else len(paragraph.split()) <= 70
                for paragraph in scope_paragraphs
            )
            if expected_set == {"off_topic"}:
                checks["only_off_topic_no_official_visa_url"] = not re.search(
                    r"https?://(?:[a-z0-9-]+\.)*gov\.uk(?:[/#?]|$)", body, re.I,
                )
                checks["only_off_topic_not_answered_with_fee_or_timing"] = (
                    "£135" not in body and not re.search(r"3\s*(?:weeks?|周)", body, re.I)
                )
                if unchanged_profile:
                    checks["only_off_topic_reply_brief"] = (
                        len(body) <= 350 if zh else len(body.split()) <= 100
                    )
                    checks["only_off_topic_no_manual_legal_review"] = (
                        case.status.value != "HUMAN_REVIEW_REQUIRED"
                        and not re.search(
                            r"人工|法律(?:复核|审核|审查)|human adviser|manual (?:legal )?review|"
                            r"legal review|lawyer|solicitor", body, re.I,
                        )
                    )
            if unchanged_profile:
                if "document_checklist" not in expected_set:
                    checks["off_topic_no_fact_change_no_default_document_requests"] = (
                        not documents and not default_document_block
                    )
                known_questions = (QUESTION_TEXT_ZH if zh else QUESTION_TEXT_EN).values()
                checks["off_topic_no_fact_change_no_new_intake_questions"] = (
                    not next_fact_questions(case)
                    and not any(question in body for question in [*questions, *known_questions])
                    and not re.search(
                        r"计划哪天到英国、哪天离开|What dates are you planning to arrive in and leave the UK",
                        body, re.I,
                    )
                )
    if not expected:
        checks["non_question_not_given_fee_or_timing"] = (
            "£135" not in body and not re.search(r"3\s*(?:weeks?|周)", body, re.I)
        )
    if development_checks:
        if not expected and unchanged_profile and not explicit_preparation_request(incoming_body):
            checks["no_question_no_fact_change_no_proactive_intake_guidance"] = (
                not {"application_overview_v1", "student_self_preparation_v1"}.intersection(
                    case.guidance_events
                ) and APPLICATION_URL not in body and "Apply now" not in body
            )
            checks["no_question_no_fact_change_no_default_document_requests"] = not default_document_block
        if expected and "document_checklist" not in expected and unchanged_profile:
            checks["pure_faq_no_unrequested_document_requests"] = not default_document_block
            checks["pure_faq_no_travel_date_reminder"] = not re.search(
                r"日期先留空|日期确定后再告诉|leave the dates open|let me know when your dates are decided",
                body, re.I,
            )
        if "document_checklist" in expected:
            requirement_sources = {url for requirement in case.requirements
                if requirement.applicable and requirement.blocker and not requirement.satisfied
                for url in requirement.source_urls}
            checks["explicit_checklist_has_requirement_sources"] = bool(requirement_sources) and all(
                url in body for url in requirement_sources
            )
        if not zh and set(case.latest_changes) == {"occupation_status"}:
            checks["occupation_correction_not_field_label"] = "Occupation Status:" not in body
        if "unsupported" in expected and case.status.value == "HUMAN_REVIEW_REQUIRED":
            review_paragraphs = [paragraph for paragraph in body.split("\n\n") if re.search(
                r"人工|核实|复核|未核验|没有核验|human adviser|separate check|verified guidance",
                paragraph, re.I,
            )]
            checks["unsupported_human_review_not_repeated"] = len(review_paragraphs) == 1
        if "timing" in expected and re.search(r"passport|护照", incoming_body, re.I):
            checks["passport_return_not_equated_with_decision"] = bool(
                re.search(r"passport|护照", body, re.I)
                and re.search(r"return|back|collect|退回|取回|拿回|返还|领取", body, re.I)
                and re.search(r"decision|决定|审理", body, re.I)
                and re.search(r"different|separate|not |cannot|can't|不等于|不是|不能|分开|不同", body, re.I)
            )
        if "translation" in expected and re.search(r"friend|朋友", incoming_body, re.I):
            checks["friend_translation_not_assumed_acceptable"] = bool(
                re.search(r"friend|who (?:has )?translated|朋友|谁.{0,5}翻译", body, re.I)
                and re.search(r"cannot|can't|not automatically|does not|isn't enough|不能|不代表|不足|无法", body, re.I)
            )
    return checks


def exercise_workflow(
    item: dict[str, Any], initial: Case, event: InboundEvent,
    proposed: CasePatch, validated: CasePatch,
    *, development_checks: bool = False,
) -> dict[str, Any]:
    """Network is disabled for the full workflow and provider-bound simulation."""
    delegate = CapturedPatchModel(proposed)
    capture = CaptureGmail()
    policy = load_policy(POLICY)
    with tempfile.TemporaryDirectory(prefix="visa-intent-probe-") as directory:
        store = SQLiteStore(Path(directory) / "case.db")
        try:
            store.save_case(initial)
            workflow = WorkflowService(
                store, policy, delegate, today_provider=lambda: REVIEWED_AS_OF,
            )
            dispatcher = OutboxDispatcher(
                store,
                AutomaticGmailReplySender(capture, store, initial.applicant_contact),
                channel="gmail",
                allowed_message_types=("blocked", "awaiting_profile_confirmation", "awaiting_confirmation"),
            )
            with (
                patch("socket.socket.connect", side_effect=AssertionError("Simulation network disabled")),
                patch("socket.create_connection", side_effect=AssertionError("Simulation network disabled")),
                patch.object(GmailAdapter, "send_reply", side_effect=AssertionError("Real Gmail forbidden")),
            ):
                case, duplicate, plan = workflow.process(event)
                outcomes = dispatcher.dispatch_due(event.received_at)
                rows = store.list_outbox()
                stored = next((row for row in rows if row["event_id"] == event.id), None)
                body = capture.calls[0]["body"] if len(capture.calls) == 1 else ""
                before = initial.profile.model_dump(mode="json")
                after = case.profile.model_dump(mode="json")
                allowed_changes = {update.field: update.value for update in validated.updates}
                expected_profile = dict(before)
                expected_profile.update(allowed_changes)
                expected_profile = CaseProfile.model_validate(expected_profile).model_dump(mode="json")
                checks = {
                    "workflow_first_processing_not_duplicate": not duplicate,
                    "workflow_extraction_did_not_fallback": not workflow.llm.last_extraction_fallback,
                    "workflow_topics_match_validated_patch": set(case.customer_question_topics)
                        == {question.topic for question in validated.customer_questions},
                    "profile_matches_only_grounded_updates": after == expected_profile,
                    "untouched_profile_fields_preserved": all(
                        after[field] == value for field, value in before.items() if field not in allowed_changes
                    ),
                    "no_profile_or_final_confirmation": not case.profile_confirmed
                        and not case.final_summary_confirmed,
                    "no_confirmation_request": plan not in {"awaiting_profile_confirmation", "awaiting_confirmation"},
                    "no_pack_or_release": case.delivery_path is None and plan != "ready",
                    "single_case_isolated": len(store.list_cases()) == 1 and case.id == initial.id,
                    "one_captured_send": len(outcomes) == 1 and outcomes[0].status == "SENT"
                        and len(capture.calls) == 1,
                    "no_attachment_sent": all(not call.get("attachment") for call in capture.calls),
                    "exact_persisted_provider_body": stored is not None and stored["payload"] == body,
                    "simulation_network_disabled": True,
                }
                # Corpus expectations are evaluator-only, independent of whatever
                # updates the extractor proposed or the guard accepted.
                for field, value in item.get("expected_profile_updates", {}).items():
                    checks[f"expected_profile_update:{field}"] = field in after and after[field] == value
                checks.update(content_checks(
                    item["expected_topics"], case, body, item["language"],
                    incoming_body=item["body"], unchanged_profile=expected_profile == before,
                    development_checks=development_checks,
                ))
                for marker in item.get("required_reply_markers", []):
                    checks[f"required_marker:{marker}"] = marker in body
                _, replayed, replay_plan = workflow.process(event)
                replay_outcomes = dispatcher.dispatch_due(event.received_at)
                checks["replay_no_extra_outbox_or_send"] = (
                    replayed and replay_plan == "duplicate_ignored"
                    and len(store.list_outbox()) == len(rows) and not replay_outcomes
                    and len(capture.calls) == 1
                )
                checks["replay_no_second_extraction"] = delegate.extract_calls == 1
            return {
                "checks": checks,
                "plan": plan,
                "case_status": case.status.value,
                "profile_before": before,
                "profile_after": after,
                "accepted_update_fields": sorted(allowed_changes),
                "workflow_topics": case.customer_question_topics,
                "customer_answers": case.customer_answers,
                "next_fact_questions": next_fact_questions(case),
                "provider_bound_body": body,
                "send_render_mode": stored["reply_render_mode"] if stored else None,
                "send_render_error": stored["reply_render_error"] if stored else None,
                "render_fallback": workflow.llm.last_render_fallback,
                "render_error": workflow.llm.last_render_error,
                "extraction_guard_error": workflow.llm.last_extraction_error,
                "captured_sends": len(capture.calls),
            }
        finally:
            store.close()


def write_report(path: Path, report: dict[str, Any], *, create: bool = False) -> None:
    with path.open("x" if create else "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def aggregate(results: list[dict[str, Any]], key: str) -> dict[str, Any]:
    evaluated = [row[key] for row in results if key in row]
    true_positive = sum(item["true_positive"] for item in evaluated)
    false_positive = sum(len(item["false_positive"]) for item in evaluated)
    false_negative = sum(len(item["false_negative"]) for item in evaluated)
    return {
        "evaluated": len(evaluated),
        "errors_without_classification": len(results) - len(evaluated),
        "exact_matches": sum(item["exact"] for item in evaluated),
        "exact_accuracy_including_errors": sum(item["exact"] for item in evaluated) / len(results)
            if results else 0.0,
        "micro_precision": true_positive / (true_positive + false_positive)
            if true_positive + false_positive else (1.0 if not false_negative else 0.0),
        "micro_recall": true_positive / (true_positive + false_negative)
            if true_positive + false_negative else 1.0,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
    }


def load_replay(
    path: Path, items: list[dict[str, Any]], corpus_hash: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Reuse only a complete, matching real development extraction, not a replay chain."""
    original_bytes = path.read_bytes()
    original = json.loads(original_bytes)
    if original.get("split") != "development" or original.get("completed") is not True:
        raise ValueError("Replay requires a completed development report")
    if original.get("real_extraction_reused_from") or original.get("new_provider_result") is False:
        raise ValueError("Replay must reference the original real extraction, not another replay")
    if original.get("model") != MODEL or original.get("corpus_sha256") != corpus_hash:
        raise ValueError("Replay model or frozen corpus hash mismatch")
    rows = original.get("results", [])
    if len(rows) != len(items) or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Replay case count or ID uniqueness mismatch")
    by_id = {row["id"]: row for row in rows}
    if set(by_id) != {item["id"] for item in items}:
        raise ValueError("Replay IDs do not exactly match the development split")
    for item in items:
        row = by_id[item["id"]]
        if (row.get("body") != item["body"] or row.get("language") != item["language"]
                or row.get("expected_topics") != item["expected_topics"]):
            raise ValueError(f"Replay body, language or expected topics mismatch: {item['id']}")
        if row.get("expected_profile_updates", {}) != item.get("expected_profile_updates", {}):
            raise ValueError(f"Replay expected profile updates mismatch: {item['id']}")
        if not any(usage.get("operation") == "extract_case_patch" for usage in row.get("usage", [])):
            raise ValueError(f"Replay row has no real extraction usage evidence: {item['id']}")
        CasePatch.model_validate(row["raw_patch"])
    return by_id, {
        "real_extraction_reused_from": str(path.resolve()),
        "original_report_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "original_source_sha256": original.get("source_sha256"),
        "original_corpus_sha256": original["corpus_sha256"],
        "original_started_at": original.get("started_at"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", required=True, choices=("development", "holdout"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--corpus", type=Path, default=CORPUS,
                        help="Frozen JSON corpus; defaults to the original adviser intent corpus")
    parser.add_argument("--allow-holdout", action="store_true", help="Explicit final evaluation only; never tune using it")
    parser.add_argument("--replay-report", type=Path,
                        help="Development only: reuse matching original real raw patches without reading a key or calling a model")
    args = parser.parse_args()
    if args.split == "holdout" and not args.allow_holdout:
        parser.error("The frozen holdout requires --allow-holdout; do not use it for development")
    if args.split == "development" and args.allow_holdout:
        parser.error("--allow-holdout is only meaningful with --split holdout")
    if args.split == "holdout" and args.replay_report:
        parser.error("Holdout cannot use --replay-report")
    if args.output.exists():
        parser.error("Choose a NEW output path; retained results cannot be overwritten")
    corpus_bytes = args.corpus.read_bytes()
    items = [item for item in json.loads(corpus_bytes)
             if bool(item.get("holdout", False)) == (args.split == "holdout")]
    if not items:
        parser.error("Selected split is empty")
    corpus_hash = hashlib.sha256(corpus_bytes).hexdigest()
    replay_rows: dict[str, dict[str, Any]] = {}
    replay_metadata: dict[str, Any] = {}
    if args.replay_report:
        try:
            replay_rows, replay_metadata = load_replay(args.replay_report, items, corpus_hash)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(str(error))
    report: dict[str, Any] = {
        "scope": ("Previously captured real patches replayed through current validation/workflow; no new provider result; Gmail capture only"
                  if args.replay_report else
                  "One real DeepSeek extraction per fictional case; reviewed workflow replies; Gmail capture only"),
        "not_proven": ["answer quality in general", "naturalness", "universal legal accuracy", "real Gmail delivery", "final pack delivery"],
        "classification_is_not_answer_quality": True,
        "split": args.split,
        "holdout_authorized": args.allow_holdout,
        "model": MODEL,
        "model_calls": 0,
        "new_provider_result": not bool(args.replay_report),
        "development_content_checks": args.split == "development",
        "reviewed_guidance_as_of": REVIEWED_AS_OF.isoformat(),
        "started_at": datetime.now(UTC).isoformat(),
        "corpus_id": (str(args.corpus.resolve().relative_to(REPOSITORY))
                      if args.corpus.resolve().is_relative_to(REPOSITORY)
                      else str(args.corpus.resolve())),
        "corpus_sha256": corpus_hash,
        "source_sha256": source_fingerprints(),
        "expected_case_count": len(items),
        "completed": False,
        "results": [],
        **replay_metadata,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.output, report, create=True)
    key: str | None = None
    model: DeepSeekStructuredLLM | None = None
    if not args.replay_report:
        key = read_secret(
            "DEEPSEEK_API_KEY", file_environment_name="DEEPSEEK_API_KEY_FILE",
            default_file=REPOSITORY / ".secrets/deepseek_api_key.txt",
        )
        if not key:
            report["startup_error"] = "Missing DeepSeek key; no API request made"
            write_report(args.output, report)
            parser.error(report["startup_error"])
        model = DeepSeekStructuredLLM(MODEL, api_key=key)
    policy = load_policy(POLICY)
    for item in items:
        row: dict[str, Any] = {
            "id": item["id"], "language": item["language"], "body": item["body"],
            "expected_topics": item["expected_topics"], "rationale": item["rationale"],
            "expected_profile_updates": item.get("expected_profile_updates", {}),
            "checks": {}, "model_calls": 0,
            "provider_result_reused": bool(args.replay_report),
        }
        usage_start = len(model.usage_history) if model is not None else 0
        started = time.perf_counter()
        try:
            initial = seed_case(item, policy.version)
            event = InboundEvent(
                id=f"inbound-{item['id']}", channel="gmail",
                external_thread_id=initial.external_thread_id,
                sender=initial.applicant_contact,
                subject="英国旅行材料咨询" if item["language"] == "zh" else "UK visit preparation",
                body=latest_reply_text(item["body"]),
                known_profile=initial.profile.model_dump(mode="json"),
                received_at=datetime.now(UTC),
                rfc_message_id=f"<inbound-{item['id']}@example.test>",
            )
            if model is not None:
                extraction_started = time.perf_counter()
                report["model_calls"] += 1
                row["model_calls"] += 1
                proposed = model.extract_case_patch(event)
                row["extraction_latency_seconds"] = round(time.perf_counter() - extraction_started, 3)
            else:
                original_row = replay_rows[item["id"]]
                if original_row.get("profile_before") != initial.profile.model_dump(mode="json"):
                    raise ValueError("Replay intake context differs from the original extraction context")
                proposed = CasePatch.model_validate(original_row["raw_patch"])
                row["extraction_latency_seconds"] = None
                row["original_extraction_latency_seconds"] = original_row.get("extraction_latency_seconds")
                row["original_usage"] = original_row.get("usage", [])
            row["raw_patch"] = proposed.model_dump(mode="json")
            validated = validate_case_patch(event, proposed)
            row["validated_patch"] = validated.model_dump(mode="json")
            raw_topics = sorted({question.topic for question in proposed.customer_questions})
            actual_topics = sorted({question.topic for question in validated.customer_questions})
            row["raw_topics"] = raw_topics
            row["actual_topics"] = actual_topics
            row["raw_topic_metrics"] = topic_metrics(item["expected_topics"], raw_topics)
            row["validated_topic_metrics"] = topic_metrics(item["expected_topics"], actual_topics)
            row["guard_rejected_questions"] = [question.model_dump(mode="json")
                for question in proposed.customer_questions if question not in validated.customer_questions]
            row["guard_rejected_or_normalized_updates"] = [update.model_dump(mode="json")
                for update in proposed.updates if update not in validated.updates]
            row["checks"]["extraction_available"] = True
            row["checks"]["raw_topics_exact"] = row["raw_topic_metrics"]["exact"]
            row["checks"]["validated_topics_exact"] = row["validated_topic_metrics"]["exact"]
            exercised = exercise_workflow(
                item, initial, event, proposed, validated,
                development_checks=args.split == "development",
            )
            row["checks"].update(exercised.pop("checks"))
            row.update(exercised)
        except Exception as error:
            row["checks"]["completed_without_error"] = False
            # Error text from a provider is retained; never print request headers or credentials.
            row["error"] = {"type": type(error).__name__,
                            "message": str(error).replace(key, "[REDACTED]") if key else str(error)}
        row["elapsed_seconds"] = round(time.perf_counter() - started, 3)
        row["usage"] = model.usage_history[usage_start:] if model is not None else []
        row["passed"] = bool(row["checks"]) and all(row["checks"].values())
        report["results"].append(row)
        write_report(args.output, report)
        failures = [name for name, passed in row["checks"].items() if not passed]
        print(item["id"], "PASS" if row["passed"] else "FAIL", ", ".join(failures), flush=True)
    report["completed"] = True
    report["finished_at"] = datetime.now(UTC).isoformat()
    report["source_unchanged_during_run"] = report["source_sha256"] == source_fingerprints()
    report["raw_classification"] = aggregate(report["results"], "raw_topic_metrics")
    report["validated_classification"] = aggregate(report["results"], "validated_topic_metrics")
    report["all_passed"] = report["source_unchanged_during_run"] and all(row["passed"] for row in report["results"])
    write_report(args.output, report)
    print("Report:", args.output, flush=True)
    if not report["all_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
