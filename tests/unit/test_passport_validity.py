from datetime import date

import pytest

from visa_agent.domain.models import Case, Document, DocumentStatus, Evidence
from visa_agent.domain.rules import passport_valid_through_stay


@pytest.mark.parametrize(
    "expiry,source,expected",
    [
        ("2026-11-17", "passport", True),
        ("2026-11-16", "passport", False),
        ("2028-01-01", "other-letter", False),
        ("not-a-date", "passport", False),
    ],
)
def test_passport_expiry_must_be_grounded_in_accepted_travel_document(
    expiry: str, source: str, expected: bool
) -> None:
    case = Case(
        id="c", external_thread_id="t", applicant_contact="a@example.test", policy_version="v"
    )
    case.profile.planned_departure_date = date(2026, 11, 17)
    case.documents.append(
        Document(
            id="passport",
            filename="passport.pdf",
            kind="passport",
            sha256="0" * 64,
            mime_type="application/pdf",
            status=DocumentStatus.ACCEPTED_FOR_REVIEW,
            source_event_id="e",
            path="passport.pdf",
        )
    )
    case.evidence.append(
        Evidence(
            id="expiry",
            fact_key="passport_expiry_date",
            value=expiry,
            source_event_id="e",
            source_document_id=source,
            source_excerpt=expiry,
            extraction_method="test",
            model_version="none",
            confidence=1,
        )
    )
    assert passport_valid_through_stay(case) is expected
