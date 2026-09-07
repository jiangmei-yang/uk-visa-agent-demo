import re

import pytest

from visa_agent.privacy.customer_copy import customer_notice, customer_receipt, reply_language


@pytest.mark.parametrize("language", ["zh", "en"])
def test_notice_has_one_language_no_model_debug_metadata_and_keeps_disclosure(language):
    text = customer_notice("deepseek", "PC-123456ABCDEF", language)
    assert "DeepSeek" in text and "AI" in text
    assert text.count("PC-123456ABCDEF") == 1
    assert "deepseek-v4" not in text and "Notice version" not in text
    assert "Information processing notice" not in text
    assert "本线程" not in text
    if language == "zh":
        assert len(text) < 450
        assert "To help prepare" not in text and "consent reference" not in text
        assert all(word in text for word in ["之前的邮件", "保存在", "导出", "删除", "撤回", "不用于训练"])
    else:
        assert not re.search(r"[\u4e00-\u9fff]", text)
        assert all(word in text for word in ["earlier messages", "retain", "export", "delete", "Withdrawal", "non-training"])
    from visa_agent.privacy.consent import _control_parts

    parsed = _control_parts(text.splitlines()[-1])
    assert parsed.action == "granted" and parsed.business_body == ""


def test_language_uses_current_reply_not_quoted_english_history():
    assert reply_language("你好，我要办理签证。\n\nOn Monday someone wrote:\nEnglish history") == "zh"
    assert reply_language("Thanks, please explain.\n\nOn Monday someone wrote:\n中文历史", "zh") == "en"


@pytest.mark.parametrize("action", ["granted", "withdrawn", "declined"])
def test_receipts_are_brief_single_language(action):
    chinese = customer_receipt(action, "zh")
    english = customer_receipt(action, "en")
    assert len(chinese) < 100 and not re.search(r"[A-Za-z]", chinese)
    assert not re.search(r"[\u4e00-\u9fff]", english)
