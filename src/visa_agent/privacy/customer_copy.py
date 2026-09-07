"""Customer-readable privacy copy; never authority to bypass the consent ledger."""

import re


def reply_language(body: str, fallback: str = "en") -> str:
    from visa_agent.workflow.conversation import latest_reply_text

    current = latest_reply_text(body)
    if re.search(r"[\u4e00-\u9fff]", current):
        return "zh"
    return "en" if re.search(r"[A-Za-z]", current) else fallback


def customer_notice(provider: str, reference: str, language: str) -> str:
    provider = {"deepseek": "DeepSeek", "hkgai": "HKGAI"}.get(provider.casefold(), provider)
    if language == "zh":
        return (
            "你好，开始整理前，先和你确认一下资料的使用方式。\n\n"
            f"这串邮件中你提供的信息和材料（包括之前的邮件）会保存在本服务中，并交由 {provider} 的 AI 服务辅助整理、检查和准备回复。"
            "只有你同意后才会开始；这不代表提交签证申请或确认最终材料。\n\n"
            "你可以随时回复“我撤回资料处理同意”，也可以申请导出或删除本地资料。撤回不会自动删除记录，也不能收回已交给服务商的数据；"
            "我们不能代服务商承诺删除或不用于训练。\n\n"
            "如果同意，请回复下面这句话，保留括号里的编号即可。也可以在同封邮件中补充问题或材料：\n"
            f"我同意按这份说明处理我提供的信息和材料（授权参考码 {reference}）。"
        )
    return (
        "Before we start, please confirm how we may use your information.\n\n"
        f"We will retain the information and documents in this email conversation, including earlier messages, and send their text to {provider}'s AI service "
        "to help organise information, check documents and prepare replies. We will only start with your agreement. "
        "This does not submit a visa application or confirm your final documents.\n\n"
        'You can reply "I withdraw my consent to processing my information" at any time, or ask us to export or delete local records. '
        "Withdrawal does not automatically delete records or recall data already sent to the provider. "
        "We cannot promise deletion or non-training on the provider's behalf.\n\n"
        "If you agree, reply with the sentence below, keeping the reference in brackets. You may include questions or documents in the same reply:\n"
        f"I consent to the processing described in this notice (consent reference {reference})."
    )


def customer_receipt(action: str, language: str) -> str:
    if action == "granted":
        return (
            "收到，我们可以开始整理这串邮件中的资料了。最终材料仍会交给你确认；如果你之前暂停了准备，我们会继续保持暂停。"
            if language == "zh" else
            "Thank you. We can now work through the information in this conversation. You will still review the final documents; if preparation was paused, it stays paused."
        )
    return (
        "收到，已停止后续资料处理和业务回复。已有记录不会自动删除；如果需要导出或删除本地资料，请告诉我们。"
        if language == "zh" else
        "Understood. Further processing and service replies have stopped. Existing records are not automatically deleted; let us know if you want local records exported or deleted."
    )
