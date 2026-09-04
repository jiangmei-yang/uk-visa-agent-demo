import pytest

from visa_agent.domain.date_evidence import canonical_date_value, date_is_grounded, has_calendar_day


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


@pytest.mark.parametrize("excerpt", [
    "我的出生日期是1998.5.12", "1998.05.12", "1998 . 5 . 12",
    "出生日期：１９９８．５．１２", "DOB: 1998/5/12", "1998-05-12",
    "我不是说了旅行日期还没定吗？我的生日是1998.5.12。",
])
def test_year_first_formats_survive_both_date_guards(excerpt: str) -> None:
    assert has_calendar_day(excerpt)
    assert date_is_grounded("1998-05-12", excerpt, allow_shared_year=False)


@pytest.mark.parametrize("value,excerpt", [
    ("1998-05-12", "5.12.1998"), ("1998-05-12", "12/5/1998"),
    ("1998-05-12", "1998.5"), ("1998-05-12", "1998.5.120"),
    ("1998-05-12", "11998.5.12"), ("1998-05-12", "1998.5.12.3"),
    ("1998-05-12", "1998.5/12"), ("1998-05-12", "1998.6.12"),
    ("1998-05-12", "2026年旅行，我的生日是5月12日"),
    ("1998-02-30", "1998.2.30"),
])
def test_date_format_support_does_not_infer_or_truncate(value: str, excerpt: str) -> None:
    assert not date_is_grounded(value, excerpt, allow_shared_year=False)


@pytest.mark.parametrize("value,expected", [
    ("1998.5.12", "1998-05-12"), ("１９９８．５．１２", "1998-05-12"),
    ("1998/5/12", "1998-05-12"), ("1998年5月12日", "1998-05-12"),
    ("1998 - 5 - 12", "1998-05-12"), ("1998-05-12", "1998-05-12"),
    ("1998.2.30", "1998.2.30"), ("5.12.1998", "5.12.1998"),
    ("1998年5月", "1998年5月"), ("1998.5/12", "1998.5/12"),
    ("1998.5.12.3", "1998.5.12.3"), ("生日是1998.5.12", "生日是1998.5.12"),
])
def test_only_explicit_valid_year_first_values_are_canonicalized(value: str, expected: str) -> None:
    assert canonical_date_value(value) == expected
