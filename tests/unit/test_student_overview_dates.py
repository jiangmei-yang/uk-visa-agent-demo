from datetime import date

import pytest

from visa_agent.domain.models import Case
from visa_agent.workflow.consultant_overview import _en_first_action, _zh_first_action


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
