import json
from datetime import date
from pathlib import Path

import pytest

from visa_agent.documents.natural import DocumentProposal, validate_document

REPORT = Path("eval_output/financial_document_deepseek_2026-09-07-v25.json")
TEXT = ("Harbour Test Bank - Sponsor Funds Statement\nAccount holder: Mina Example.\n"
        "Account ending: 9876.\nClosing balance HKD 88,000.00 as of 2026-08-31.")


def proposal():
    report = json.loads(REPORT.read_text())
    row = next(row for row in report['results'] if row['filename'] == 'fictional_sponsor_statement.pdf')
    assert not row['passed']  # Keep the original provider failure, never overwrite it.
    return DocumentProposal.model_validate_json(row['raw_model_response'])


def test_saved_provider_omissions_are_completed_from_exact_grounded_quotes():
    candidate = proposal()
    before = candidate.model_dump_json()
    item = candidate.financial_observations[0]
    assert 'as_of' not in item.model_fields_set and 'account_reference' not in item.model_fields_set
    result = validate_document(candidate, [TEXT], method='saved_provider_replay', version='deepseek-v4-flash')
    assert not result.requires_review
    assert result.financial_observations[0].as_of == date(2026, 8, 31)
    assert result.financial_observations[0].account_reference == '9876'
    assert candidate.model_dump_json() == before


@pytest.mark.parametrize('updates', [
    {'as_of': None}, {'account_reference': None}, {'as_of': date(2026, 9, 1)},
    {'account_reference': '9999'}, {'confidence': 0.9}, {'date_page': 2}, {'account_page': 2},
    {'date_excerpt': 'as of 2026-02-31'}, {'date_excerpt': 'Travel date: 2026-08-31'},
    {'date_excerpt': 'as of 2026-08-31 or 2026-09-01'},
    {'account_excerpt': 'Account ending: 9876 or 9999.'},
    {'account_excerpt': 'Account ending: 9999.'},
])
def test_completion_never_overrides_explicit_values_or_unreliable_sources(updates):
    candidate = proposal()
    candidate.financial_observations[0] = candidate.financial_observations[0].model_copy(update=updates)
    with pytest.raises(ValueError):
        validate_document(candidate, [TEXT], method='text', version='test')
