"""Reception survives the real outbox composer; transport is captured, not sent."""

import pytest
from test_consultant_value import Conversation, _patch


@pytest.mark.parametrize("body", ["你好", "你是谁", "请问你们是做什么的？"])
def test_gmail_boundary_delivers_reception_then_answers_business(tmp_path, monkeypatch, body):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider or mailbox access in this regression")

    monkeypatch.setattr("socket.socket.connect", forbidden)
    dialogue = Conversation(tmp_path)
    opening = dialogue.turn(body, _patch())
    assert "这里是英国签证咨询服务" in opening.body
    assert "出行目的" not in opening.body
    followup = dialogue.turn("我想去英国旅游", _patch(
        updates=[("visit_purpose", "tourism", "去英国旅游")],
    ))
    assert followup.case.id == opening.case.id
    assert followup.case.profile.visit_purpose == "tourism"
    assert "这里是英国签证咨询服务" not in followup.body
    assert len(dialogue.gmail.calls) == 2
