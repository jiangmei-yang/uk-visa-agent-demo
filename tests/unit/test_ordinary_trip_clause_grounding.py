"""Regressions from an operator-driven fictional Gmail enquiry, with counterexamples."""

import pytest

from tests.unit.test_profile_enum_evidence import checked

BODY = ("您好，我是中国护照，在香港读书，准备从香港申请。"
        "护照姓名是 Lin Chen，出生日期是1997年4月18日。"
        "想今年11月去伦敦旅游一周，具体哪天还没定，费用自己承担，预算大约2200英镑。"
        "下一步我先准备什么？")


@pytest.mark.parametrize("field,value,excerpt", [
    ("visit_purpose", "tourism", "想今年11月去伦敦旅游一周"),
    ("funding_source", "self", "费用自己承担"),
])
def test_exact_customer_message_retains_both_independent_facts(field, value, excerpt):
    result = checked(BODY, field, value, excerpt)
    assert [(u.field, u.value) for u in result.updates] == [(field, value)]


@pytest.mark.parametrize("text", [
    "想今年11月去伦敦旅游一周", "打算秋天去英国度假", "准备明年3月到伦敦观光",
])
def test_omitted_subject_in_current_applicant_plan(text):
    assert checked(text, "visit_purpose", "tourism", text).updates


@pytest.mark.parametrize("wrapper", [
    "如果{statement}，需要什么？", "我朋友说：{statement}。不是我的情况。",
    "请翻译“{statement}”。", "不{statement}。", "可能{statement}。",
])
def test_omitted_subject_does_not_admit_nonassertions(wrapper):
    text = wrapper.format(statement="想今年11月去伦敦旅游一周")
    assert not checked(text, "visit_purpose", "tourism", "想今年11月去伦敦旅游一周").updates


@pytest.mark.parametrize("prefix", ["具体哪天还没定", "旅行日期不确定", "日期还没确定"])
def test_date_uncertainty_is_not_funding_uncertainty(prefix):
    body = prefix + "，费用自己承担。"
    assert checked(body, "funding_source", "self", "费用自己承担").updates


@pytest.mark.parametrize("body", [
    "可能，费用自己承担。", "资金来源还没定，费用自己承担。",
    "日期和资金来源不确定，费用自己承担。", "如果日期还没定，费用自己承担。",
    "朋友说日期还没定，费用自己承担。", "日期还没定，费用不是自己承担。",
])
def test_uncertain_conditional_or_negated_payer_is_not_a_fact(body):
    assert not checked(body, "funding_source", "self", "费用自己承担").updates
