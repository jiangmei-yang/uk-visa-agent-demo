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


class CaptureGmail(GmailAdapter):
    def __init__(self) -> None:
        self.bodies: list[str] = []

    def send_reply(self, **kwargs: Any) -> dict[str, str]:
        self.bodies.append(kwargs['body'])
        return {"id": f"capture-{len(self.bodies)}"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new report path; retained evidence must not be overwritten')
    key = read_secret('DEEPSEEK_API_KEY', file_environment_name='DEEPSEEK_API_KEY_FILE',
                      default_file=Path('.secrets/deepseek_api_key.txt'))
    if not key:
        parser.error('Missing DeepSeek key')
    report: dict[str, Any] = {'scope': 'real DeepSeek extraction; fictional text; captured Gmail sends',
        'model': 'deepseek-v4-flash', 'completed': False, 'results': []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as output:
        json.dump(report, output)
    for language, messages in JOURNEYS.items():
        with tempfile.TemporaryDirectory(prefix='visa-gmail-probe-') as directory:
            store = SQLiteStore(Path(directory) / 'case.db')
            model = ExtractionModel('deepseek-v4-flash', api_key=key)
            workflow = WorkflowService(store, load_policy(Path('knowledge/uk_standard_visitor_2026-02-25.yaml')),
                model, today_provider=lambda: date(2026, 9, 4))
            capture = CaptureGmail()
            dispatcher = OutboxDispatcher(store,
                AutomaticGmailReplySender(capture, store, 'fictional@example.test'), channel='gmail',
                allowed_message_types=('blocked', 'awaiting_profile_confirmation', 'awaiting_confirmation'))
            try:
                for index, body in enumerate(messages):
                    now = datetime.now(UTC)
                    event = InboundEvent(id=f'{language}-{index}', external_thread_id=language,
                        sender='fictional@example.test', channel='gmail', subject='UK travel plans',
                        body=body, received_at=now)
                    case, _, plan = workflow.process(event)
                    extracted_without_fallback = not workflow.llm.last_extraction_fallback
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
                    stored = next(row for row in store.list_outbox() if row['event_id'] == event.id)
                    checks['exact_persisted_body'] = stored['payload'] == reply
                    count = len(store.list_outbox())
                    _, duplicate, _ = workflow.process(event)
                    checks['replay_no_send'] = duplicate and len(store.list_outbox()) == count and dispatcher.dispatch_due(now) == []
                    report['results'].append({'language': language, 'turn': index + 1, 'input': body,
                        'profile': case.profile.model_dump(mode='json'), 'checks': checks, 'plan': plan,
                        'provider_bound_body': reply, 'usage': model.usage_history[-1:]})
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
