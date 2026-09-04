from visa_agent.domain.models import Case, GateResult, Requirement
from visa_agent.review_ui import render_case, render_empty_page


def test_incomplete_real_case_does_not_borrow_the_fixture_story_or_claim_completion():
    case = Case(id="real-case", external_thread_id="thread", applicant_contact="fictional@example.test",
                primary_channel="gmail", policy_version="v")
    case.requirements = [Requirement(id="passport", title="Passport needed", blocker=True,
        applicable=True, satisfied=False, rule_version="v", source_urls=[])]
    gate = GateResult(allowed=False, checks={"profile_confirmed": False}, reasons=["Missing facts"])
    body = render_case(case, gate)
    assert "Lin Chen" not in body and "Recorded sample walkthrough" not in body
    assert "Still needed" in body and "<strong>Complete</strong>" not in body
    assert "£0" not in body and "Recorded trip details" in body
    assert "1 deterministic case checks" in body
    assert "not a live inbox" in body
    assert "does not show whether the Gmail or WhatsApp worker is connected" in body
    assert 'data-download href=' not in body


def test_gate_alone_cannot_enable_download_without_archive_verification():
    case = Case(id="case", external_thread_id="thread", applicant_contact="fictional@example.test",
                policy_version="v", delivery_path="unverified.zip")
    body = render_case(case, GateResult(allowed=True, checks={}, reasons=[]))
    assert 'data-download href=' not in body
    assert "not available for download" in body


def test_empty_page_offers_an_offline_action_without_promising_to_restore_deleted_data():
    body = render_empty_page()
    assert 'href="/try"' in body and "No email or WhatsApp messages will be sent" in body
    assert "Restarting does not restore a deleted case" in body
    assert "It will rebuild the case" not in body
