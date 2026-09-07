"""Short reception turns are not requests to start the intake questionnaire."""

import re


def reception_message(body: str, language: str) -> str | None:
    from visa_agent.workflow.conversation import latest_reply_text

    text = latest_reply_text(body).strip()
    # Match the whole current reply: a greeting preceding a substantive question
    # must not swallow that question, nor may quoted greetings trigger reception.
    greeting = r"(?:(?:你好|您好|嗨|hello|hi|hey)[，,！!。.\s]*)?"
    identity = r"(?:请问[，,\s]*)?(?:你是谁|您是谁|你们是谁|你是做什么的|你们是做什么的|怎么称呼你|who are you|what do you do)"
    help_request = r"(?:你能帮我什么|你们能帮我什么|你能做什么|可以帮我什么|what can you help (?:me )?with|how can you help me)"
    automated = r"(?:请问[，,\s]*)?(?:你是(?:AI|ai|人工智能|机器人|真人|人工客服)吗|你是真人还是机器人|are you (?:an? )?(?:AI|bot|robot|human)|are you a human or a bot)"
    punctuation = r"[？?！!。.\s]*"
    if re.fullmatch(greeting + automated + punctuation, text, re.I):
        return (
            "我是英国签证服务的智能助手，不是真人客服。可以帮您了解申请步骤、整理材料和检查遗漏。您想先了解哪方面？"
            if language == "zh" else
            "I'm the UK visa service's automated assistant, not a human adviser. I can help explain the application steps, organise documents and check for missing information. What would you like help with?"
        )
    if text and re.fullmatch(greeting + "(?:" + identity + "|" + help_request + ")?" + punctuation, text, re.I):
        # Exclude punctuation-only input; do not impersonate a human or claim a licence.
        if not re.search(r"[A-Za-z\u4e00-\u9fff]", text):
            return None
        return (
            "您好，这里是英国签证咨询服务。我可以帮您了解申请流程、梳理所需材料，也可以帮您检查已经准备好的资料。您有什么想咨询的？"
            if language == "zh" else
            "Hello, you've reached the UK visa preparation service. I can help you understand the application process, work out which documents you need and check what you've prepared. What would you like help with?"
        )
    return None
