"""Service interaction is an operator policy, never fabricated customer consent."""

import pytest

from tests.unit.test_processing_consent import event
from visa_agent.privacy.consent import ConsentLedger, ProcessingConsentRequired, ProcessingScope
from visa_agent.privacy.customer_copy import service_information
from visa_agent.storage.sqlite import SQLiteStore


@pytest.fixture
def ledger(tmp_path):
    store = SQLiteStore(tmp_path / "service.db")
    ledger = ConsentLedger(store)
    ledger.configure(ProcessingScope("Example", "offline", interaction="service_request"))
    yield ledger
    store.close()


def test_personal_request_proceeds_without_notice_or_fabricated_grant(ledger):
    result = ledger.handle(event(1, "我的生日是1997年7月1日，日期还没定。"), "p")
    assert result.action == "allow" and not result.granted
    case = ledger.store.get_case(result.case_id)
    assert ledger.allowed(case)
    assert ledger.store.list_outbox() == []
    assert ledger.store.connection.execute(
        "SELECT 1 FROM processing_consent_events WHERE action='granted'").fetchone() is None
    assert ledger._record(case.id)["status"] == "unknown"


def test_withdrawal_stops_even_after_model_change(ledger):
    result = ledger.handle(event(1), "p")
    case = ledger.store.get_case(result.case_id)
    ledger.handle(event(2, "请停止处理我的资料"), "p")
    assert not ledger.allowed(case)
    ledger.configure(ProcessingScope("Example", "new-model", interaction="service_request"))
    assert not ledger.allowed(case)
    assert ledger.handle(event(3, "好的，继续"), "p").action == "defer"
    assert all(row["message_type"] != "processing_notice" for row in ledger.store.list_outbox())


def test_sender_cannot_use_another_customers_thread(ledger):
    ledger.handle(event(1), "p")
    with pytest.raises(ProcessingConsentRequired):
        ledger.handle(event(2, sender="other@example.test"), "p")


@pytest.mark.parametrize("body", ["停止处理", "不用再处理我的资料了", "请停止处理我的资料"])
def test_natural_stop_does_not_require_a_special_sentence(ledger, body):
    result = ledger.handle(event(1), "p")
    assert ledger.handle(event(2, body), "p").action == "control"
    assert not ledger.allowed(ledger.store.get_case(result.case_id))


def test_existing_notices_cannot_be_silently_bypassed(tmp_path):
    store = SQLiteStore(tmp_path / "existing.db")
    ledger = ConsentLedger(store)
    ledger.configure(ProcessingScope("Example", "offline"))
    result = ledger.handle(event(1, "My date of birth is 1990-01-01."), "p")
    with pytest.raises(ProcessingConsentRequired, match="fresh state"):
        ledger.configure(ProcessingScope("Example", "offline", interaction="service_request"))
    assert not ledger.allowed(store.get_case(result.case_id))
    store.close()


@pytest.mark.parametrize("language", ["zh", "en"])
def test_information_has_no_code_or_agreement_instruction(language):
    text = service_information("deepseek", language)
    assert "DeepSeek" in text
    assert "PC-" not in text and "授权参考码" not in text and "If you agree" not in text
