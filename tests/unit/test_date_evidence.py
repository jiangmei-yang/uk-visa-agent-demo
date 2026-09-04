import pytest

from visa_agent.domain.date_evidence import date_is_grounded


@pytest.mark.parametrize(
    "value,excerpt,expected",
    [
        ("2026-11-09", "2026 年 11 月 9 日到英国", True),
        ("2026-11-11", "2026 年 11 月 9 日到英国，11 月 11\n日离开", True),
        ("2027-11-11", "2026年11月9日到英国，11月11日离开", False),
        ("2026-11-11", "11月11日离开", False),
        ("2026-11-01", "November", False),
        ("2026-11-09", "2026/11/9", True),
        ("2026-11-09", "9 November 2026", True),
        ("2026-11-09", "November 9, 2026", True),
        ("2026-11-09", "19 November 2026", False),
        ("2026-11-09", "2026-11-19", False),
        ("2026-11-11", "2026年12月1日或2027年11月11日", False),
        ("2027-11-11", "2026年12月1日或2027年11月11日", True),
    ],
)
def test_date_value_must_match_quoted_date(value: str, excerpt: str, expected: bool) -> None:
    assert date_is_grounded(value, excerpt) is expected


def test_birth_date_cannot_borrow_a_trip_year() -> None:
    assert not date_is_grounded(
        "2026-04-18", "2026年11月9日旅行，我的生日是4月18日", allow_shared_year=False
    )
    assert date_is_grounded("1997-04-18", "1997 年 4 月 18 日出生", allow_shared_year=False)
