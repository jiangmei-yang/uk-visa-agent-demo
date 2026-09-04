"""Shared semantic checks for retained financial-document excerpts.

These checks bind a proposed value to its printed label inside the quoted
excerpt. They do not authenticate a document or decide whether funds suffice.
"""

from __future__ import annotations

import re
import unicodedata

from visa_agent.domain.date_evidence import date_is_grounded


def _normalise(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _exact(value: str) -> str:
    """Match a complete normalized value, not a substring of another token."""
    return rf"(?<![\w]){re.escape(_normalise(value))}(?![\w])"


def financial_fields_are_coherent(
    *,
    kind: str,
    subject_name: str,
    amount: str,
    currency: str,
    period: str,
    basis: str,
    as_of: str,
    account_reference: str | None,
    subject_excerpt: str,
    amount_excerpt: str,
    date_excerpt: str,
    account_excerpt: str | None,
) -> bool:
    """Return whether each value is locally bound to the right printed role."""
    subject_text = _normalise(subject_excerpt)
    subject = _exact(subject_name)
    if kind == "closing_balance":
        subject_role = (
            r"(?:account holder|account name|customer name|账户持有人|账户名称|户名)"
            rf"\s*(?::|：|=|-|is|为)?\s*{subject}"
        )
    elif kind == "salary":
        subject_role = (
            r"(?:employee(?: name)?|employed person|salary for|pay for|员工|雇员|受雇人)"
            rf"\s*(?::|：|=|-|is|为)?\s*{subject}"
        )
    else:
        return False
    if re.search(subject_role, subject_text) is None:
        return False

    sign = "-" if amount.startswith("-") else ""
    plain_amount = amount.removeprefix("-")
    integer, dot, decimals = plain_amount.partition(".")
    raw_number = rf"(?<![-\d.,]){re.escape(sign + plain_amount)}(?!\d|[.,]\d)"
    grouped_value = sign + f"{int(integer):,}" + (dot + decimals if dot else "")
    grouped_number = rf"(?<![-\d.,]){re.escape(grouped_value)}(?!\d|[.,]\d)"
    currency_pattern = {
        "GBP": r"\bgbp\b|£",
        "CNY": r"\bcny\b|\brmb\b|人民币",
        "HKD": r"\bhkd\b|hk\$|港币|港元",
        "USD": r"\busd\b|us\$",
        "EUR": r"\beur\b|€",
    }.get(currency)
    if currency_pattern is None:
        return False
    money = (
        rf"(?:{currency_pattern})\s*[:=]?\s*(?:{raw_number}|{grouped_number})|"
        rf"(?:{raw_number}|{grouped_number})\s*(?:{currency_pattern})"
    )
    amount_text = _normalise(amount_excerpt)
    if re.search(money, amount_text) is None:
        return False
    if kind == "closing_balance":
        if period != "closing" or basis != "unspecified":
            return False
        if re.search(r"\b(?:closing|ending) balance\b|期末余额|结单余额", amount_text) is None:
            return False
    else:
        if period not in {"annual", "monthly"}:
            return False
        if re.search(r"\b(?:salary|pay|wage)\b|工资|薪资|年薪|月薪", amount_text) is None:
            return False
        period_pattern = (
            r"\b(?:annual|yearly|per year|a year)\b|每年|年薪"
            if period == "annual"
            else r"\b(?:monthly|per month|a month)\b|每月|月薪"
        )
        if re.search(period_pattern, amount_text) is None:
            return False
        basis_pattern = {"gross": r"\bgross\b|税前", "net": r"\bnet\b|税后"}.get(basis)
        if basis_pattern is not None and re.search(basis_pattern, amount_text) is None:
            return False

    date_text = _normalise(date_excerpt)
    date_role = (
        r"\b(?:statement date|as of|closing balance date)\b|结单日期|截至|余额日期"
        if kind == "closing_balance"
        else r"\b(?:letter date|letter dated|dated|pay statement date|pay date|issued on)\b|信函日期|工资单日期|签发日期"
    )
    if re.search(date_role, date_text) is None or not date_is_grounded(as_of, date_excerpt):
        return False

    if account_reference is None:
        return account_excerpt is None
    if account_excerpt is None:
        return False
    account_text = _normalise(account_excerpt)
    account_role = (
        r"(?:account(?:\s+(?:number|no\.?|reference|ending))?|a/c(?:\s+(?:no\.?)?)?|"
        r"账户(?:号码|号)?|账号|卡号|尾号)"
        rf"\s*(?::|：|=|-|is|为)?\s*{_exact(account_reference)}"
    )
    return re.search(account_role, account_text) is not None
