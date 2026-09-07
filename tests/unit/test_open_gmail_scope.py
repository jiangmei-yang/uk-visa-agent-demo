from email.message import EmailMessage

import pytest

from visa_agent.channels.gmail_intake import scope_rejection


@pytest.mark.parametrize("sender,headers,accepted", [
    ("anyone@example.test", {}, True),
    ("new.person@another.test", {}, True),
    ("service@example.test", {}, False),
    ("mailer-daemon@example.test", {}, False),
    ("postmaster@example.test", {}, False),
    ("anyone@example.test", {"Auto-Submitted": "auto-replied"}, False),
    ("anyone@example.test", {"Precedence": "bulk"}, False),
    ("anyone@example.test", {"List-Id": "example"}, False),
    ("a@example.test, b@example.test", {}, False),
])
def test_open_intake_filters_machine_mail_and_ambiguous_senders(sender, headers, accepted):
    message = EmailMessage()
    message["From"], message["To"] = sender, "service@example.test"
    for name, value in headers.items():
        message[name] = value
    assert (scope_rejection(message, None, "service@example.test", None) is None) is accepted
