"""Real-model fictional conversations through the automatic Gmail sender; no Gmail network calls."""

import argparse
import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from visa_agent.channels.automatic_reply import AutomaticGmailReplySender
from visa_agent.channels.gmail import GmailAdapter
from visa_agent.channels.outbound import OutboxDispatcher
from visa_agent.domain.models import InboundEvent
from visa_agent.domain.policy import load_policy
from visa_agent.llm.deepseek_client import DeepSeekStructuredLLM
from visa_agent.llm.guarded import deterministic_fallback_message
from visa_agent.secrets import read_secret
from visa_agent.storage.sqlite import SQLiteStore
from visa_agent.workflow.adviser_guidance import APPLICATION_URL, DOCUMENTS_URL
from visa_agent.workflow.conversation import next_fact_questions, reply_items
from visa_agent.workflow.service import WorkflowService

JOURNEYS = {
    "zh": [
        "你好，我持中国护照，准备从香港申请英国旅游签证。",
        "我在读大学，自己承担费用，日期还没定。请先把需要的材料清单发给我。",
        "刚才说错了，其实是去参加会议，学校出钱。定了2026年11月10日抵英、11月17日离英。",
        "如果资料没问题就继续，但我还没有检查摘要。",
    ],
    "en": [
        "Hello, I hold a Chinese passport and will apply from Hong Kong for a holiday in the UK.",
        "I'm a university student, paying for the trip myself. My dates are not fixed yet. "
        "Please send me the document checklist first.",
        "Sorry, I got that wrong: it's a conference and my university is paying. "
        "I'll arrive on 10 November 2026 and leave on 17 November 2026.",
        "If the details are okay, proceed, but I haven't checked the summary yet.",
    ],
}


class ExtractionModel(DeepSeekStructuredLLM):
    # The actual automatic sender overwrites model prose; do not spend on unused generation here.
    render_message = staticmethod(deterministic_fallback_message)


class DraftProbeModel(DeepSeekStructuredLLM):
    raw_reply: str | None = None

    def render_message(self, case, plan):
        self.raw_reply = super().render_message(case, plan)
        return self.raw_reply


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.bodies.append(kwargs['body'])
        return {"id": f"capture-{len(self.bodies)}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--semantic-intent', action='store_true',
                        help='Use indirect undecided-travel expressions rather than keyword variants')
    parser.add_argument('--model-prose', action='store_true',
                        help='Capture real model prose before the guard and automatic Gmail replacement')
    parser.add_argument('--guarded-sending', action='store_true',
                        help='Capture the opt-in revalidated workflow-draft sending path')
    parser.add_argument('--question-frontier', action='store_true',
                        help='Test unanswered-question pause, explicit resumption and later identity facts')
    parser.add_argument('--adviser-followups', action='store_true',
                        help='After the question frontier, test natural website, fee, bank and checklist questions')
    args = parser.parse_args()
    if args.adviser_followups and not args.question_frontier:
        parser.error('--adviser-followups requires --question-frontier')
    if args.output.exists():
        parser.error('Choose a new report path; retained evidence must not be overwritten')
    key = read_secret('DEEPSEEK_API_KEY', file_environment_name='DEEPSEEK_API_KEY_FILE',
                      default_file=Path('.secrets/deepseek_api_key.txt'))
    if not key:
        parser.error('Missing DeepSeek key')
    report: dict[str, Any] = {'scope': 'real DeepSeek extraction; fictional text; captured Gmail sends',
        'model': 'deepseek-v4-flash', 'semantic_intent': args.semantic_intent,
        'model_prose': args.model_prose, 'guarded_sending': args.guarded_sending,
        'question_frontier': args.question_frontier,
        'adviser_followups': args.adviser_followups,
        'completed': False, 'results': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(report, output)
    for language, messages in JOURNEYS.items():
        messages = list(messages)
        if args.question_frontier:
            messages.extend([
                '现在可以继续了，下一步需要什么？' if language == 'zh'
                else "I'm ready to continue. What is the next step?",
                '护照上的姓名是示例申请人，我的生日是1998.5.12。' if language == 'zh'
                else 'My passport name is Example Applicant. My date of birth is 1998.5.12.',
            ])
        if args.adviser_followups:
            messages.extend([
                '申请网页在哪？' if language == 'zh'
                else 'Where is the official visa application website?',
                '网址发我一下' if language == 'zh'
                else 'Could you send me that link again?',
                '签证费是多少钱？银行流水要提供几个月的？' if language == 'zh'
                else 'How much is the visa fee, and how many months of bank statements do I need?',
                '材料要准备些什么？' if language == 'zh'
                else 'Which documents should I prepare?',
            ])
        if args.semantic_intent:
            messages[1] = (
                '我在读大学，自己出钱。得等学校公布假期安排之后，才能告诉你哪天出发和回来。请先把需要的材料清单发给我。'
                if language == 'zh' else "I'm a university student and paying for the trip myself. "
                "My trip has to wait for the university to publish the holiday schedule; I can't give "
                "arrival or departure days yet. Please send me the document checklist first."
            )
        with tempfile.TemporaryDirectory(prefix='visa-gmail-probe-') as directory:
            store = SQLiteStore(Path(directory) / 'case.db')
            model = (DraftProbeModel if args.model_prose else ExtractionModel)('deepseek-v4-flash', api_key=key)
            workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')),
                model, today_provider=lambda: date(2026, 9, 4))
            capture = CaptureGmail()
            dispatcher = OutboxDispatcher(store,
                AutomaticGmailReplySender(capture, store, 'fictional@example.test',
                    allow_guarded_drafts=args.guarded_sending), channel='gmail',
                allowed_message_types=('blocked', 'awaiting_profile_confirmation', 'awaiting_confirmation'))
            try:
                for index, body in enumerate(messages):
                    if isinstance(model, DraftProbeModel):
                        model.raw_reply = None
                    usage_start = len(model.usage_history)
                    now = datetime.now(UTC)
                    event = InboundEvent(id=f'{language}-{index}', external_thread_id=language,
                        sender='fictional@example.test', channel='gmail', subject='UK travel plans',
                        body=body, received_at=now)
                    case, _, plan = workflow.process(event)
                    extracted_without_fallback = not workflow.llm.last_extraction_fallback
                    guarded_draft = next(row['payload'] for row in store.list_outbox() if row['event_id'] == event.id)
                    render_fallback = workflow.llm.last_render_fallback
                    render_error = workflow.llm.last_render_error
                    outcomes = dispatcher.dispatch_due(now)
                    reply = capture.bodies[-1] if len(capture.bodies) == index + 1 else ''
                    checks = {
                        'one_captured_send': len(outcomes) == 1 and outcomes[0].status == 'SENT'
                            and len(capture.bodies) == index + 1,
                        'extraction_available': extracted_without_fallback,
                        'routine_not_escalated': case.status.value != 'HUMAN_REVIEW_REQUIRED',
                        'purpose': case.profile.visit_purpose == ('tourism' if index < 2 else 'conference'),
                        'language': case.customer_language == language,
                        'no_release': not case.delivery_path and not case.final_summary_confirmed,
                    }
                    if index == 0:
                        checks['official_application_entry_provided'] = APPLICATION_URL in reply
                        checks['guidance_before_questions'] = bool(case.customer_answers and
                            reply.index(case.customer_answers[0]) < reply.index(reply_items(case)[1][0]))
                    if index == 1:
                        checks['student_funding_explained'] = (
                            ('在读证明' in reply and '银行流水' in reply
                             and any(text in reply for text in ('预算数字本身不能代替资金证明', '预算金额本身不是证明')))
                            if language == 'zh' else
                            ('student' in reply and 'bank statements' in reply
                             and any(text in reply for text in ('a budget figure is not funding evidence',
                                                                'a budget figure alone is not evidence'))))
                        # A source citation is not a repeated application introduction.
                        checks['application_intro_not_repeated'] = (
                            '先给你申请入口' not in reply and 'Here is the official starting point' not in reply)
                    if index >= 1:
                        checks['student'] = case.profile.occupation_status == 'student'
                        checks['funding'] = case.profile.funding_source == ('self' if index == 1 else 'employer_or_school')
                    if index == 1:
                        questions = reply_items(case)[1]
                        documents = reply_items(case)[2]
                        checks['requested_checklist_answered'] = len(documents) >= 4 and all(d in reply for d in documents)
                        checks['list_before_questions'] = bool(documents and questions and
                            documents[0] in reply and questions[0] in reply and reply.index(documents[0]) < reply.index(questions[0]))
                        checks['unknown_dates_retained'] = case.profile.planned_arrival_date is None and case.profile.planned_departure_date is None
                        checks['unknown_dates_deferred'] = not {'planned_arrival_date', 'planned_departure_date'} & set(next_fact_questions(case))
                    if index >= 2:
                        checks['corrected_dates'] = (case.profile.planned_arrival_date == date(2026, 11, 10)
                            and case.profile.planned_departure_date == date(2026, 11, 17))
                    if args.question_frontier:
                        if index == 2:
                            checks['specific_payer_preserved'] = (
                                '费用由谁承担：学校' in reply and '雇主或学校' not in reply
                                if language == 'zh' else 'university' in reply and 'employer or school' not in reply)
                        if index in {2, 3}:
                            checks['unanswered_questions_not_reasked'] = (
                                next_fact_questions(case) == [] and '?' not in reply and '？' not in reply)
                        if index == 4:
                            checks['explicit_resumption_one_field'] = len(next_fact_questions(case)) == 1
                        if index == 5:
                            checks['later_identity_retained'] = (case.profile.date_of_birth == date(1998, 5, 12)
                                and bool(case.profile.full_name)
                                and not {'full_name', 'date_of_birth'} & set(next_fact_questions(case)))
                    if args.adviser_followups and index >= 6:
                        checks['known_identity_retained'] = (
                            case.profile.date_of_birth == date(1998, 5, 12)
                            and bool(case.profile.full_name))
                        checks['followup_not_mistaken_for_waiting'] = (
                            '等你方便补充资料' not in reply
                            and "pick this up when you're ready" not in reply
                            and 'remaining details' not in reply)
                        checks['followup_does_not_reask_known_facts'] = (
                            not {'full_name', 'date_of_birth', 'planned_arrival_date',
                                 'planned_departure_date'} & set(next_fact_questions(case))
                            and all(question not in reply for question in [
                                '你的出生日期是什么', '计划哪天', 'What is your date of birth',
                                'What dates are you planning to arrive',
                            ]))
                        checks['followup_does_not_restart_unanswered_intake'] = next_fact_questions(case) == []
                        if index in {6, 7}:
                            checks['explicit_application_question_answered'] = (
                                APPLICATION_URL in reply and 'Apply now' in reply)
                        if index == 8:
                            checks['reviewed_visitor_fee_answered'] = (
                                '£135' in reply and '6' in reply and 'Standard Visitor' in reply
                                and APPLICATION_URL in reply)
                            checks['bank_period_answered_without_fixed_month_rule'] = (
                                '没有统一规定银行流水必须提供几个月' in reply
                                and '资金来源' in reply and '不能只凭' in reply
                                if language == 'zh' else 'does not set one fixed number of months' in reply
                                and 'funds come from' in reply and 'months alone' in reply)
                            checks['bank_guidance_has_official_source'] = DOCUMENTS_URL in reply
                        if index == 9:
                            documents = reply_items(case)[2]
                            questions = reply_items(case)[1]
                            checks['natural_checklist_question_answered'] = (
                                len(documents) >= 4 and all(document in reply for document in documents))
                            checks['checklist_before_any_followup_question'] = (
                                bool(documents) and (not questions or (
                                    documents[0] in reply and questions[0] in reply
                                    and reply.index(documents[0]) < reply.index(questions[0]))))
                    stored = next(row for row in store.list_outbox() if row['event_id'] == event.id)
                    checks['exact_persisted_body'] = stored['payload'] == reply
                    count = len(store.list_outbox())
                    _, duplicate, _ = workflow.process(event)
                    checks['replay_no_send'] = duplicate and len(store.list_outbox()) == count and dispatcher.dispatch_due(now) == []
                    report['results'].append({'language': language, 'turn': index + 1, 'input': body,
                        'profile': case.profile.model_dump(mode='json'), 'checks': checks, 'plan': plan,
                        'deferred_fields': case.deferred_fields,
                        'pending_question_fields': case.pending_question_fields,
                        'question_plan': case.question_plan,
                        'raw_model_draft': getattr(model, 'raw_reply', None),
                        'guarded_model_draft': guarded_draft, 'render_fallback': render_fallback,
                        'render_error': render_error,
                        'send_render_mode': stored['reply_render_mode'],
                        'send_render_error': stored['reply_render_error'],
                        'provider_bound_body': reply, 'usage': model.usage_history[usage_start:]})
                    print(language, index + 1, 'PASS' if all(checks.values()) else 'FAIL', flush=True)
                    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
            finally:
                store.close()
    report['completed'] = True
    report['all_passed'] = all(all(row['checks'].values()) for row in report['results'])
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    if not report['all_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
