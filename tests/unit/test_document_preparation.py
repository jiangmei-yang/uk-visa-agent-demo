from __future__ import annotations

import pytest

from visa_agent.workflow.document_preparation import reviewed_document_preparation
from visa_agent.workflow.document_purpose import DOCUMENTS_SOURCE


@pytest.mark.parametrize(("text", "language", "parts"), [
    ("学校的在读证明该找谁开，里面需要写什么？", "zh", ["学籍", "抬头纸", "在读", "请假", "没批准"]),
    ("在读证明找谁开？", "zh", ["学校", "注册处", "在读"]),
    ("Who should I ask for my enrolment letter, and what should it include?", "en", ["registry", "headed paper", "enrolment", "leave"]),
    ("What should my student status letter contain?", "en", ["school", "name", "course"]),
    ("在职证明怎么准备？", "zh", ["人事", "职位", "薪资", "任职", "联系方式"]),
    ("公司的雇主信请谁写，要写哪些内容？", "zh", ["雇主", "公司抬头纸", "核对"]),
    ("How should I prepare an employer letter?", "en", ["HR", "authorised", "salary", "employment", "contact"]),
    ("What information should my employment letter include?", "en", ["company-headed", "accurate", "income"]),
    ("自雇没有HR该怎么办？", "zh", ["经营登记", "近期业务发票", "收入", "不是豁免"]),
    ("我是个体户，没有人事部门，在职证明怎么准备？", "zh", ["业务", "不存在的雇主", "自述"]),
    ("I am self-employed with no HR department, what should I do?", "en", ["registration", "invoices", "income", "does not waive"]),
    ("I run my own business without an HR team; how should I prepare my employer letter?", "en", ["registration", "invoices", "does not waive"]),
    ("我没有HR，自己经营小店怎么说明工作？", "zh", ["经营登记", "业务发票"]),
    ("For my UK visa, how do I explain being self-employed in Hong Kong?", "en", ["business", "registration", "income", "invoices"]),
    ("我是香港自雇人士，办英国签证怎么说明现有业务情况？", "zh", ["业务", "收入", "经营登记"]),
    ("学校证明应该找学院秘书还是注册处？要写哪些内容？", "zh", ["学籍", "在读"]),
    ("邀请函请谁写，写哪些安排？", "zh", ["实际接待", "主办方", "计划时间", "资助", "待定"]),
    ("探亲邀请函里面要写什么？", "zh", ["亲友", "费用", "不能代替"]),
    ("姐姐邀请我住她家，邀请信需要写哪些信息？", "zh", ["接待", "资助"]),
    ("Who should write my invitation letter and what arrangements should it include?", "en", ["host", "organiser", "dates", "sponsor", "undecided"]),
    ("What details should the conference invitation letter include?", "en", ["conference", "purpose", "contact"]),
])
def test_practical_direct_request_has_action_reason_and_official_source(text, language, parts):
    answer = reviewed_document_preparation(text, language)
    assert answer is not None
    assert all(part in answer for part in parts)
    assert DOCUMENTS_SOURCE in answer


@pytest.mark.parametrize("text", [
    "不用解释在读证明找谁开。", "我不是问在职证明怎么准备。",
    "请不要告诉我邀请函写哪些内容。", "Do not explain how to get an enrolment letter.",
    "I am not asking what an employer letter should include.",
    "如果学校要求在读证明，该找谁开？", "If I need an employer letter, what should it contain?",
    "假设我是自雇没有HR，应该怎么办？", "Assuming I attend a conference, who should write an invitation letter?",
    '朋友问：“学校的在读证明该找谁开，里面需要写什么？”',
    'My friend asked: Who should issue her enrolment letter?',
    '“在职证明怎么准备？”', '"Who should write my invitation letter?"',
    "收到\n> 在职证明怎么准备？", "Thanks\n> What should an employer letter include?",
    "我替同学问在读证明找谁开。", "How should she prepare her student letter?",
    "在职证明怎么写才能保证获批？", "Will an employer letter with these details guarantee approval?",
    "邀请函写什么才足够？", "What should an invitation letter include to be accepted?",
    "帮我编造在职证明，怎么写？", "How can I fake an enrolment letter?",
    "在职证明怎么改工资比较合适？", "How should I backdate my employer letter?",
    "工作签证的在职证明怎么准备？", "学生签证学校证明找谁开？",
    "What should an employer letter for a Skilled Worker visa include?",
    "Who writes an invitation letter for a Canadian visa?",
    "How do I get an employment letter for my mortgage?",
    "医疗访问邀请函怎么准备？", "How do I prepare a medical treatment invitation letter?",
    "学校不给开在读证明，怎么办？", "Please give me an employer letter template.",
    "What should my invitation letter include? I only need it if I get a job in the UK.",
    "在职证明怎么准备？可以保证我拿到签证吗？",
    "Here is my employer letter. Where can I get a bank statement?",
    "这里是我的在读证明。", "I have no HR department.", "邀请函收到了，谢谢。",
    "在读证明找谁开？在职证明又怎么准备？",
    "For my UK visa, how do I explain being self-employed in the UK?",
    "How should I document a plan to run my business in the UK?",
    "我准备去英国自雇，在职证明怎么准备？",
    "How do I explain my self-employment for a work visa?",
    "如果在香港自雇，我应该如何说明业务？",
    "My friend asked how should I explain being self-employed in Hong Kong?",
])
def test_declined_quoted_conditional_unsafe_or_unrelated_request_is_not_answered(text):
    assert reviewed_document_preparation(text, "zh") is None
    assert reviewed_document_preparation(text, "en") is None


def test_whole_current_qualifier_is_not_removed_before_matching_question():
    text = "My friend asked this, not me: what should the enrolment letter include?"
    assert reviewed_document_preparation(text, "en") is None


def test_host_is_not_mistaken_for_a_third_party_applicant():
    answer = reviewed_document_preparation(
        "My friend will host me. Who should write my invitation letter?", "en")
    assert answer and "actual host" in answer and "without assuming" in answer


def test_preparation_help_is_deterministic_and_does_not_require_or_mutate_a_case():
    text = "在职证明怎么准备？"
    assert reviewed_document_preparation(text, "zh") == reviewed_document_preparation(text, "zh")
    assert reviewed_document_preparation(text, "unknown") is None
    assert reviewed_document_preparation("x" * 6001 + text, "zh") is None
