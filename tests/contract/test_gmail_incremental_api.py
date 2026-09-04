from types import SimpleNamespace

import pytest

from visa_agent.channels.gmail import GmailAdapter, GmailHistoryExpiredError
from visa_agent.channels.outbound import PermanentChannelError, TransientChannelError


class FakeService:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def users(self):
        return self

    def history(self):
        return self

    def messages(self):
        return self

    def getProfile(self, **kwargs):
        return self

    def list(self, **kwargs):
        self.calls.append(kwargs)
        return self

    def execute(self):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_history_reads_added_entries_only_and_preserves_continuation():
    service = FakeService({"historyId": "98765432109876543210", "nextPageToken": "next",
        "history": [{"messages": [{"id": "not-an-addition"}],
                     "messagesDeleted": [{"message": {"id": "deleted"}}],
                     "messagesAdded": [{"message": {"id": "new"}},
                                       {"message": {"id": "new"}}]}]})
    page = GmailAdapter(service).list_added_history_page("123", "page-two")
    assert page.added_message_ids == ("new",)
    assert page.next_page_token == "next"
    assert page.history_id == "98765432109876543210"
    assert service.calls == [{"userId": "me", "startHistoryId": "123", "maxResults": 100,
                             "historyTypes": ["messageAdded"], "pageToken": "page-two"}]


def test_empty_history_is_a_valid_terminal_page():
    page = GmailAdapter(FakeService({"historyId": "222"})).list_added_history_page("111")
    assert page.added_message_ids == () and page.next_page_token is None


@pytest.mark.parametrize("status, expected", [(404, GmailHistoryExpiredError),
    (401, PermanentChannelError), (403, PermanentChannelError), (429, TransientChannelError),
    (503, TransientChannelError)])
def test_only_history_404_requests_full_resync(status, expected):
    error = RuntimeError("provider detail not for application logs")
    error.resp = SimpleNamespace(status=status)
    with pytest.raises(expected):
        GmailAdapter(FakeService(error)).list_added_history_page("111")


@pytest.mark.parametrize("value", [None, "", "0", "-1", "１", 123])
def test_invalid_checkpoint_response_cannot_advance(value):
    with pytest.raises(ValueError):
        GmailAdapter(FakeService({"historyId": value})).list_added_history_page("111")


def test_scoped_full_sync_page_keeps_query_and_token_without_total_cap():
    service = FakeService({"messages": [{"id": "old"}], "nextPageToken": "more"})
    page = GmailAdapter(service).list_message_page("from:applicant@example.test after:123", "page")
    assert page.message_ids == ("old",) and page.next_page_token == "more"
    assert service.calls[0]["q"] == "from:applicant@example.test after:123"
    assert service.calls[0]["includeSpamTrash"] is False
    assert service.calls[0]["pageToken"] == "page"


def test_profile_history_id_remains_an_exact_string():
    assert GmailAdapter(FakeService({"historyId": "12345678901234567890"})).current_history_id() == "12345678901234567890"
