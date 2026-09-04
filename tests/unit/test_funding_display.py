import pytest

from visa_agent.domain.models import Case, Evidence, ProvenanceState
from visa_agent.workflow.conversation import (
    change_acknowledgement,
    confirmation_message,
    received_context,
)


@pytest.mark.parametrize("language,excerpt,expected,old_enum", [
    ("zh", "学校出钱", "学校", "雇主或学校"),
    ("en", "my university is paying", "university", "employer or school"),
])
def test_specific_payer_survives_acknowledgement_correction_and_summary(language, excerpt, expected, old_enum):
    case = Case(id="c", external_thread_id="t", applicant_contact="fictional@example.test", policy_version="v",
                customer_language=language)
    case.profile.funding_source = "employer_or_school"
    case.latest_received_facts = {"funding_source": "employer_or_school"}
    case.latest_changes = {"funding_source": "employer_or_school"}
    case.evidence.append(Evidence(id="e", fact_key="funding_source", value="employer_or_school",
        source_event_id="event", source_excerpt=excerpt, extraction_method="bounded_structured_extraction",
        model_version="test", confidence=1.0, provenance_state=ProvenanceState.EXTRACTED_UNVERIFIED))
    before = case.model_dump_json()
    for text in (received_context(case), change_acknowledgement(case), confirmation_message(case, profile_only=True)):
        assert expected in text and old_enum not in text
    assert case.model_dump_json() == before
