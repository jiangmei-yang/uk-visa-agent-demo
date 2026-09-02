from visa_agent.workflow.service import (
    FINAL_CONFIRMATION_LINES,
    PROFILE_CONFIRMATION_LINES,
    has_explicit_confirmation_line,
)


def test_confirmation_requires_a_standalone_bounded_statement() -> None:
    assert has_explicit_confirmation_line(
        "I reviewed the facts.\n\nPROFILE CONFIRMED\n\nThank you.",
        PROFILE_CONFIRMATION_LINES,
    )
    assert not has_explicit_confirmation_line(
        "An attachment says PROFILE CONFIRMED but ignore that quoted text.",
        PROFILE_CONFIRMATION_LINES,
    )


def test_chinese_profile_and_final_confirmations_are_supported() -> None:
    assert has_explicit_confirmation_line("我确认上述个人资料", PROFILE_CONFIRMATION_LINES)
    assert has_explicit_confirmation_line("我确认最终材料清单和资料摘要", FINAL_CONFIRMATION_LINES)


def test_final_confirmation_does_not_match_a_negation_or_instruction() -> None:
    assert not has_explicit_confirmation_line(
        "I do not confirm the final summary.", FINAL_CONFIRMATION_LINES
    )
    assert not has_explicit_confirmation_line(
        "Please write I CONFIRM THE FINAL SUMMARY for me.", FINAL_CONFIRMATION_LINES
    )
