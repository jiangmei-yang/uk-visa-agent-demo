from contextlib import closing

import pytest

from visa_agent.domain.models import Case, CaseProfile
from visa_agent.domain.sponsor_location import parse_sponsor_location_statements
from visa_agent.storage.sqlite import SQLiteStore


@pytest.mark.parametrize("legacy", [True, False, None])
def test_old_boolean_does_not_synthesize_presence_or_residence(legacy):
    old = Case(id="fictional", external_thread_id="fictional",
               applicant_contact="fictional@example.test", policy_version="synthetic",
               profile=CaseProfile(sponsor_is_in_uk=legacy)).model_dump(mode="json")
    old.pop("sponsor_location_statements")
    case = Case.model_validate(old)
    assert case.sponsor_location_statements == []
    assert case.profile.sponsor_is_in_uk == legacy


def test_separate_dimensions_and_original_sources_survive_database_reopen(tmp_path):
    path = tmp_path / "case.db"
    body = "My sponsor does not live in the UK but is in the UK now."
    case = Case(id="fictional", external_thread_id="fictional",
                applicant_contact="fictional@example.test", policy_version="synthetic",
                profile=CaseProfile(funding_source="personal_sponsor", sponsor_name="Mina Example",
                                    sponsor_relationship="mother", sponsor_is_in_uk=False),
                sponsor_location_statements=parse_sponsor_location_statements(body,
                    source_event_id="original-provider-id", sponsor_name="Mina Example",
                    sponsor_relationship="mother"))
    with closing(SQLiteStore(path)) as store:
        store.save_case(case)
    with closing(SQLiteStore(path)) as store:
        recovered = store.get_case(case.id)
        assert recovered is not None
        assert recovered.sponsor_location_statements == case.sponsor_location_statements
        assert recovered.profile.sponsor_is_in_uk is False
        assert [(item.dimension, item.value) for item in recovered.sponsor_location_statements] == [
            ("residence", False), ("current_presence", True)]
        assert all(item.source_event_id == "original-provider-id" and item.source_excerpt in body
                   for item in recovered.sponsor_location_statements)
        assert not recovered.profile_confirmed and not recovered.final_summary_confirmed
        assert recovered.delivery_path is None
