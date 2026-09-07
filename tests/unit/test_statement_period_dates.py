import pytest

from visa_agent.domain.financial_evidence import _statement_period_end


@pytest.mark.parametrize("value,excerpt,expected", [
    ("2026-08-31", "Statement period: 1 August 2026 to 31 August 2026", True),
    ("2026-08-31", "Period covered: 2026-08-01 through 2026-08-31", True),
    ("2026-08-01", "Statement period: 1 August 2026 to 31 August 2026", False),
    ("2026-08-31", "Statement period: 1 September 2026 to 31 August 2026", False),
    ("2026-08-31", "Statement period: 1 August to 31 August", False),
    ("2026-08-31", "Trip period: 1 August 2026 to 31 August 2026", False),
    ("2026-08-31", "Statement period: 1 August 2026 to 31 August 2026 or 30 September 2026", False),
])
def test_statement_period_requires_explicit_unambiguous_end(value, excerpt, expected):
    assert _statement_period_end(value, excerpt) is expected
