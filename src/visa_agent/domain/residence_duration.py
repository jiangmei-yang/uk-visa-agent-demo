"""Literal current-home duration evidence, not inferred dates or postal validation."""

import re


def residence_duration_is_grounded(value: object, excerpt: str, body: str, *, sent_question_verified: bool = False) -> bool:
    if not isinstance(value, str) or not value.strip() or value not in excerpt or excerpt not in body:
        return False
    if sent_question_verified and body.strip().rstrip("。.") == value.strip() and excerpt.strip().rstrip("。.") == value.strip():
        # Reuse the duration grammar without manufacturing an evidence excerpt.
        check = f"I have lived at my current address for {value}."
        return residence_duration_is_grounded(value, check, check)
    # Interpret the enclosing current sentence so a clipped quote cannot drop
    # its owner, negation or hypothetical qualification.
    sentences = [part.strip() for part in re.split(r"[。！？!?;；\n]|\.(?:\s|$)", body) if excerpt.rstrip("。.!?") in part]
    if len(sentences) != 1:
        return False
    context = sentences[0]
    # A trailing contrast rejects the OLD amount, not the preceding affirmative
    # duration. Keep the original excerpt unchanged; field/value linkage below
    # still rejects a model selecting the amount after 'not'.
    intent_context = re.sub(r",\s*not\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
                            r"(?:\s+(?:years?|months?))?\s*$", "", context, flags=re.I)
    if re.search(r"[\"“”「」]|\b(?:if|unless|might|would|will|not|never|said|says|told|unsure)\b|"
                 r"如果|假如|假设|假設|不是|并非|並非|不确定|不確定|记不清|記不清|没住|沒住|打算|计划|計劃", intent_context, re.I):
        return False
    if re.search(re.escape(context) + r"\s*[?？]", body):
        return False
    current_home = re.search(
        r"\bI(?:\s+have|'ve|’ve)?\s+(?:lived|been living)\s+at\s+my\s+current\s+(?:home|address)\b|"
        r"我(?:在)?(?:目前|现在|現在|现|現)(?:的)?(?:住址|地址|家)(?:已经|已經|已)?住|"
        r"我在(?:这个|這個)(?:住址|地址|家)(?:已经|已經|已)?住|"
        r"(?:^|[，,])\s*(?:我)?(?:现在|目前)住(?:在)?[^。！？;；,，]{2,150}[，,]\s*(?:已经|已)?住", context, re.I)
    duration = re.fullmatch(r"(?:(?:about|around|approximately)\s+)?(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
                            r"\s+(?:years?|months?)(?:\s+and\s+\d+\s+months?)?|"
                            r"(?:大概|大约|大約|约|約)?[\d一二三四五六七八九十两兩]+(?:年(?:半|[\d一二三四五六七八九十]+个月)?|个?月)(?:左右)?",
                            value.strip(), re.I)
    return bool(current_home and duration and re.match(
        r"\s*(?:for\s+|了)?" + re.escape(value) + r"(?:\b|[，,。\s]|$)", context[current_home.end():], re.I))
