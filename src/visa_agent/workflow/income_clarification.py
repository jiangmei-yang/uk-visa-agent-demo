"""Small reviewed explanations, not applicant financial findings or tax advice.

GOV.UK supporting-documents guide, reviewed 2026-09-06, sections 2 and opening
non-guarantee notice. Matching transfers is a practical source-of-funds check;
it does not classify actual transactions or decide that funding is sufficient.
"""

import re

from visa_agent.workflow.advice_preferences import _current_clauses

SOURCE = "https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk"


def income_question(text: str) -> str | None:
    """Recognise a complete current question; never infer a financial fact."""
    clauses = _current_clauses(text)
    for clause in clauses:
        if re.fullmatch(
            r"(?:what should I use to explain my income|"
            r"没有工资单(?:的话)?(?:我)?用什么说明收入)[?？]?", clause.strip(), re.I,
        ):
            return "income_evidence"
    current = " ".join(clauses)
    if (re.search(r"\btransfers from my own (?:other )?account\b", current, re.I)
            and any(re.fullmatch(r"is the whole amount my income[?？]?", c.strip(), re.I) for c in clauses)):
        return "own_transfers"
    return None


def guarantee_question(text: str) -> bool:
    return any(re.fullmatch(
        r"can you guarantee (?:this|these documents?|these records?) (?:is|are) enough[?？]?|"
        r"你能保证这些材料够了吗[?？]?", clause.strip(), re.I,
    ) for clause in _current_clauses(text))


def income_answer(kind: str, language: str, *, self_employed: bool) -> str | None:
    if kind == "income_evidence" and self_employed:
        return (
            "你是自雇，不用硬套工资单的格式。可以从现有的经营登记材料或近期业务发票开始，"
            "再用对应账户的收款记录说明收入来源。先把业务凭证和实际入账对应起来；"
            "这只是准备思路，还需要核对具体文件。"
            if language == "zh" else
            "As you're self-employed, start with existing business registration documents or recent invoices, "
            "rather than trying to produce employee payslips. Match the business records to the relevant receipts "
            "in your account so the source of earnings is clear. These are preparation suggestions; "
            "the actual documents still need checking."
        )
    if kind == "own_transfers":
        return (
            "不能把所有入账直接当作收入。同一笔钱在你自己的账户之间转移，不会仅因再次入账就成为新增收入。"
            "可以把转出和转入两边的记录对应起来，另外说明这笔钱最初来自哪里。"
            "这不代表已经认定你的资金足够，也不是税务上的收入判定。"
            if language == "zh" else
            "No—the total deposits are not necessarily income. Moving the same money between your own accounts "
            "does not by itself create new earnings. Match the outgoing and incoming records and separately explain "
            "where the money originally came from. This does not establish that your funds are sufficient "
            "and is not a tax classification."
        )
    return None


def guarantee_answer(language: str) -> str:
    return (
        "我知道你想有个明确答案，但不能为了让你放心就说材料一定够或一定获批。"
        "现在能做的是核对文件是否把你的情况和资金来源解释清楚，指出仍缺的部分；最终由 UKVI 决定。"
        if language == "zh" else
        "I understand you'd like a clear answer, but I can't guarantee that the records are enough or that the visa "
        "will be approved. What I can do is check whether they explain your circumstances and the source of funds, "
        "and identify gaps. UKVI makes the decision."
    )
