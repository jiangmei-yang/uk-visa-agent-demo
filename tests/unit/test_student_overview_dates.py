from datetime import date

import pytest

from visa_agent.domain.models import Case, Document, DocumentStatus
from visa_agent.workflow.consultant_overview import (
    _en_first_action,
    _zh_first_action,
    comprehensive_overview_requested,
)


@pytest.mark.parametrize("body", [
    "在读证明、行程和银行流水我已经发了，请帮我再核对一下。具体是哪份文件的哪项信息有问题？我好按你说的补充，不想把全部材料再准备一遍。",
    "我不想再看完整材料清单，请告诉我哪里写错了。",
    "我不打算把全部文件重发，具体缺哪份？",
])
def test_customer_declining_repeat_preparation_does_not_request_full_overview(body):
    case = Case(id="decline-repeat", external_thread_id="decline-repeat",
        applicant_contact="sample@example.test", policy_version="test")
    case.profile.visit_purpose = "tourism"
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    case.profile.nationality_country = "China"
    case.profile.application_country = "Hong Kong"
    assert not comprehensive_overview_requested(case, body)
    assert comprehensive_overview_requested(case, "请把全部材料清单一次说清楚。")


@pytest.mark.parametrize("dates_known", [True, False])
@pytest.mark.parametrize("language", ["zh", "en"])
def test_student_advice_does_not_call_confirmed_dates_undecided(dates_known, language):
    case = Case(id="dates", external_thread_id="dates", applicant_contact="sample@example.test",
                policy_version="test")
    case.profile.occupation_status = "student"
    case.profile.funding_source = "self"
    if dates_known:
        case.profile.planned_arrival_date = date(2026, 11, 10)
        case.profile.planned_departure_date = date(2026, 11, 17)
    body = (_zh_first_action if language == "zh" else _en_first_action)(case)
    phrase = "日期还没定" if language == "zh" else "before fixing the travel dates"
    assert (phrase in body) is not dates_known


@pytest.mark.parametrize("language", ["zh", "en"])
@pytest.mark.parametrize("status", [DocumentStatus.ACCEPTED_FOR_REVIEW, DocumentStatus.SUPERSEDED,
                                    DocumentStatus.NEEDS_REPLACEMENT])
def test_student_next_action_does_not_request_an_already_read_letter(language, status):
    case = Case(id="received", external_thread_id="received", applicant_contact="sample@example.test",
                policy_version="test")
    case.profile.occupation_status = "student"
    case.documents.append(Document(id="letter", filename="student.pdf", kind="student_letter",
        sha256="a" * 64, mime_type="application/pdf", status=status,
        source_event_id="received", path="/fictional/student.pdf"))
    body = (_zh_first_action if language == "zh" else _en_first_action)(case)
    receipt = "在读证明已经收到" if language == "zh" else "I've received your enrolment letter"
    assert (receipt in body) == (status == DocumentStatus.ACCEPTED_FOR_REVIEW)
