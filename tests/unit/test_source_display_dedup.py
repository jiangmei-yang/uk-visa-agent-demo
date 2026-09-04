from visa_agent.workflow.service import (
    _without_previously_sent_source_lines,
)

SOURCE = (
    "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/"
    "guide-to-supporting-documents-visiting-the-uk"
)


def test_prior_sent_page_suppresses_the_same_long_source_with_a_new_fragment() -> None:
    answer = "Prepare the relevant evidence.\nGOV.UK: " + SOURCE + "#if-you-have-a-sponsor"
    prior = [{"status": "SENT", "payload": "Earlier guidance\nGOV.UK: " + SOURCE}]

    result = _without_previously_sent_source_lines(answer, prior, "What should I prepare next?")

    assert result == "Prepare the relevant evidence."


def test_explicit_repeat_request_keeps_a_previously_sent_source() -> None:
    answer = "Here it is.\nGOV.UK: " + SOURCE
    prior = [{"status": "SENT", "payload": "Earlier guidance\nGOV.UK: " + SOURCE}]

    result = _without_previously_sent_source_lines(
        answer,
        prior,
        "Could you send the official page link again?",
    )

    assert result == answer


def test_unsent_or_failed_rows_do_not_claim_the_customer_saw_a_source() -> None:
    answer = "Prepare the relevant evidence.\nGOV.UK: " + SOURCE
    prior = [{"status": "FAILED", "payload": "GOV.UK: " + SOURCE}]

    assert _without_previously_sent_source_lines(answer, prior, "What next?") == answer
