"""A relative, host or UK location does not establish the applicant's sponsor."""

from datetime import UTC, datetime

import pytest

from visa_agent.domain.models import InboundEvent
from visa_agent.llm.guarded import validate_case_patch
from visa_agent.llm.ports import CasePatch, FactUpdate


def event(body: str, *, known: dict | None = None, requested: list[str] | None = None) -> InboundEvent:
    return InboundEvent(
        id="fictional-sponsor-role", external_thread_id="fictional-sponsor-role",
        sender="fictional@example.test", subject="Fictional visit preparation", body=body,
        received_at=datetime(2026, 9, 4, tzinfo=UTC), known_profile=known or {},
        requested_fields=requested or [],
    )


def update(field: str, value: str | bool, excerpt: str, confidence: float = 1) -> FactUpdate:
    return FactUpdate(field=field, value=value, source_excerpt=excerpt, confidence=confidence)


def validate(body: str, updates: list[FactUpdate], **context) -> CasePatch:
    return validate_case_patch(event(body, **context), CasePatch(updates=updates, ambiguities=[]))


def test_literal_example_surname_is_not_a_hypothetical_marker():
    body = "My mother Mei Example is paying for my trip."
    result = validate(body, [update("sponsor_name", "Mei Example", body),
                             update("sponsor_relationship", "mother", body)])
    assert {item.field: item.value for item in result.updates} == {
        "sponsor_name": "Mei Example", "sponsor_relationship": "mother"}


@pytest.mark.parametrize("prefix", ["For example, ", "Example: ", "As an example, "])
def test_explicit_example_framing_still_does_not_supply_sponsor(prefix):
    body = prefix + "my mother Mei Example is paying for my trip."
    assert not validate(body, [update("sponsor_name", "Mei Example", body),
                               update("sponsor_relationship", "mother", body)]).updates


@pytest.mark.parametrize("full_excerpt", [True, False])
def test_payment_replacement_binds_new_relative_not_displaced_relative(full_excerpt):
    body = "My father Jian Example is paying for my trip instead of my mother."
    excerpt = body if full_excerpt else "My father Jian Example is paying for my trip"
    result = validate(body, [update("sponsor_name", "Jian Example", excerpt),
                             update("sponsor_relationship", "father", excerpt)])
    assert {item.field: item.value for item in result.updates} == {
        "sponsor_name": "Jian Example", "sponsor_relationship": "father"}
    assert not validate(body, [update("sponsor_relationship", "mother", body)]).updates


@pytest.mark.parametrize(("body", "funding", "relationship", "location"), [
    ("我在香港上班，去英国探望姐姐，住她家。旅行费用自己付。", "旅行费用自己付", "探望姐姐", "去英国探望姐姐"),
    ("I work in Hong Kong. I will visit my sister in the UK and stay with her. I pay my own travel costs.",
     "I pay my own travel costs.", "my sister", "my sister in the UK"),
])
def test_host_only_sponsor_proposals_are_omitted_without_losing_self_funding(
    body, funding, relationship, location,
):
    result = validate(body, [update("funding_source", "self", funding),
                             update("sponsor_relationship", "sister", relationship),
                             update("sponsor_is_in_uk", True, location)])
    assert [(item.field, item.value) for item in result.updates] == [("funding_source", "self")]
    assert not result.requires_human_review and result.ambiguities == []


@pytest.mark.parametrize(("body", "field", "value", "excerpt"), [
    ("I will visit my sister Mei Chen in Britain.", "sponsor_name", "Mei Chen", "Mei Chen"),
    ("My sister lives in the UK.", "sponsor_is_in_uk", True, "My sister lives in the UK."),
    ("我姐姐不是我的资助人。", "sponsor_relationship", "sister", "我姐姐"),
    ("My sister is not my sponsor.", "sponsor_relationship", "sister", "My sister"),
    ("If my sister pays for my trip, I will ask her for a letter.", "sponsor_relationship", "sister", "my sister"),
    ("如果姐姐资助我，我会找她要证明。", "sponsor_relationship", "sister", "姐姐"),
    ("My sister used to pay for my trips.", "sponsor_relationship", "sister", "My sister"),
    ("以前姐姐承担我的旅行费用。", "sponsor_relationship", "sister", "姐姐"),
    ("My sister will pay for my friend's trip.", "sponsor_relationship", "sister", "My sister"),
    ("姐姐承担我朋友的旅行费用。", "sponsor_relationship", "sister", "姐姐"),
    ("My friend's sponsor is Mei Chen.", "sponsor_name", "Mei Chen", "Mei Chen"),
    ("收到。\n\nOn Friday, Adviser wrote:\n我的资助人是姐姐。", "sponsor_relationship", "sister", "我的资助人是姐姐"),
])
def test_noncurrent_nonapplicant_or_nonfunding_roles_are_not_new_sponsor_facts(body, field, value, excerpt):
    result = validate(body, [update(field, value, excerpt)])
    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(("body", "field", "value", "excerpt"), [
    ("姐姐承担我的旅行费用。", "sponsor_relationship", "sister", "姐姐"),
    ("My sister will pay for my trip.", "sponsor_relationship", "sister", "My sister"),
    ("My sponsor is Mei Chen.", "sponsor_name", "Mei Chen", "Mei Chen"),
    ("我的资助人叫陈梅。", "sponsor_name", "陈梅", "陈梅"),
    ("My sponsor lives in the UK.", "sponsor_is_in_uk", True, "My sponsor lives in the UK."),
    ("My sponsor does not live in the UK.", "sponsor_is_in_uk", False, "My sponsor does not live in the UK."),
    ("我的资助人不住在英国。", "sponsor_is_in_uk", False, "我的资助人不住在英国。"),
    ("My sister will pay for my trip. My sister lives in the UK.", "sponsor_is_in_uk", True, "My sister lives in the UK."),
    ("姐姐承担我的旅行费用。姐姐住在英国。", "sponsor_is_in_uk", True, "姐姐住在英国"),
])
def test_explicit_current_applicant_sponsor_role_supports_its_own_facts(body, field, value, excerpt):
    result = validate(body, [update(field, value, excerpt)])
    assert len(result.updates) == 1 and result.updates[0].source_excerpt == excerpt
    assert result.updates[0].value == value and not result.requires_human_review


def test_funding_enum_is_not_role_proof_and_does_not_bind_another_relative():
    result = validate("My mother will pay for my trip. I am visiting my sister in the UK.", [
        update("funding_source", "personal_sponsor", "My mother will pay for my trip."),
        update("sponsor_relationship", "sister", "my sister"),
        update("sponsor_is_in_uk", True, "my sister in the UK"),
    ])
    assert [item.field for item in result.updates] == ["funding_source"]
    invented_role = validate("I am visiting my sister in the UK.", [
        update("funding_source", "personal_sponsor", "visiting my sister"),
        update("sponsor_relationship", "sister", "my sister"),
    ])
    assert not any(item.field.startswith("sponsor_") for item in invented_role.updates)


@pytest.mark.parametrize(("body", "proposed_value", "excerpt"), [
    ("她不资助我。", "self", "她不资助我"),
    ("My sister is not paying for my trip.", "self", "My sister is not paying for my trip"),
    ("我住姐姐家，但她不承担费用。", "self", "她不承担费用"),
    ("I will pay for the trip myself.", "personal_sponsor", "I will pay for the trip myself"),
    ("我父亲会承担这次旅费。", "self", "我父亲会承担这次旅费"),
])
def test_funding_enum_must_match_an_affirmative_payer_statement(
    body: str,
    proposed_value: str,
    excerpt: str,
) -> None:
    result = validate(body, [update("funding_source", proposed_value, excerpt)])

    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(("body", "value", "excerpt"), [
    ("这次旅行费用由我自己承担。", "self", "由我自己承担"),
    ("I will pay for the trip from my own savings.", "self", "pay for the trip from my own savings"),
    (
        "I am a student and will pay for the trip myself.",
        "self",
        "will pay for the trip myself",
    ),
    ("我父亲会承担这次旅费。", "personal_sponsor", "我父亲会承担这次旅费"),
    ("My mother will pay for my trip.", "personal_sponsor", "My mother will pay for my trip"),
    ("公司会承担我这次旅行的机票和住宿。", "employer_or_school", "公司会承担我这次旅行的机票和住宿"),
    ("My university will cover my travel costs.", "employer_or_school", "My university will cover my travel costs"),
    (
        "My university will pay directly for flights and accommodation.",
        "employer_or_school",
        "My university will pay directly for flights and accommodation",
    ),
])
def test_explicit_affirmative_funding_source_still_passes(
    body: str,
    value: str,
    excerpt: str,
) -> None:
    result = validate(body, [update("funding_source", value, excerpt)])

    assert [(item.field, item.value) for item in result.updates] == [("funding_source", value)]
    assert not result.requires_human_review


@pytest.mark.parametrize(("body", "value"), [
    ("自费", "self"),
    ("My employer", "employer_or_school"),
    ("My father", "personal_sponsor"),
])
def test_short_funding_value_uses_only_the_sent_funding_question(body: str, value: str) -> None:
    candidate = update("funding_source", value, body)

    assert validate(body, [candidate], requested=["funding_source"]).updates == [candidate]
    if body == "自费":
        assert validate(body, [candidate]).updates == [candidate]
    else:
        assert validate(body, [candidate]).updates == []


@pytest.mark.parametrize(("conditional", "location", "current"), [
    ("If my plans change, my sister will pay for my trip.", "My sister lives in the UK.",
     "My sister is now my sponsor."),
    ("如果计划有变，姐姐承担我的旅费。", "姐姐住在英国。", "现在我的资助人是姐姐。"),
])
def test_condition_scope_survives_a_comma_but_not_a_later_independent_role(conditional, location, current):
    relation = "my sister" if conditional.startswith("If") else "姐姐"
    result = validate(conditional + " " + location, [
        update("sponsor_relationship", "sister", relation),
        update("sponsor_is_in_uk", True, location),
    ])
    assert result.updates == [] and not result.requires_human_review
    declared = validate(conditional + " " + location + " " + current, [
        update("sponsor_relationship", "sister", current),
        update("sponsor_is_in_uk", True, location),
    ])
    assert len(declared.updates) == 2 and not declared.requires_human_review


@pytest.mark.parametrize(("body", "field", "value"), [
    ("My sister", "sponsor_relationship", "sister"),
    ("Mei Chen", "sponsor_name", "Mei Chen"),
    ("No", "sponsor_is_in_uk", False),
])
def test_sent_sponsor_question_with_known_funding_role_supports_a_short_answer(body, field, value):
    candidate = update(field, value, body)
    result = validate(body, [candidate], known={"funding_source": "personal_sponsor"}, requested=[field])
    assert result.updates == [candidate]
    assert validate(body, [candidate], known={"funding_source": "personal_sponsor"}).updates == []
    assert validate(body, [candidate], requested=[field]).updates == []


@pytest.mark.parametrize(
    ("body", "proposed", "accepted"),
    [
        ("Yes", True, True),
        ("Yes", False, False),
        ("No", False, True),
        ("No", True, False),
        ("在英国", True, True),
        ("在英国", False, False),
        ("不在英国", False, True),
        ("不在英国", True, False),
    ],
)
def test_sent_sponsor_location_short_answer_must_match_boolean_polarity(
    body: str,
    proposed: bool,
    accepted: bool,
) -> None:
    candidate = update("sponsor_is_in_uk", proposed, body)
    result = validate(
        body,
        [candidate],
        known={"funding_source": "personal_sponsor"},
        requested=["sponsor_is_in_uk"],
    )

    assert (result.updates == [candidate]) is accepted
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "proposed"),
    [
        ("My sponsor does not live in the UK.", True),
        ("My sponsor lives in the UK.", False),
        ("我的资助人不在英国。", True),
        ("我的资助人住在英国。", False),
    ],
)
def test_full_sponsor_location_statement_also_rejects_opposite_boolean(
    body: str,
    proposed: bool,
) -> None:
    candidate = update("sponsor_is_in_uk", proposed, body.rstrip("。"))
    result = validate(body, [candidate])

    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "relationship", "name"),
    [
        ("My father Jian Chen refused to pay for my trip.", "father", "Jian Chen"),
        ("My mother Mei Chen is unable to cover my travel costs.", "mother", "Mei Chen"),
        ("我父亲陈建国拒绝支付这次旅行费用。", "father", "陈建国"),
        ("我姐姐陈梅无法承担我的旅费。", "sister", "陈梅"),
    ],
)
def test_refusal_or_inability_to_pay_cannot_create_personal_sponsor_facts(
    body: str,
    relationship: str,
    name: str,
) -> None:
    result = validate(
        body,
        [
            update("funding_source", "personal_sponsor", body.rstrip("。")),
            update("sponsor_relationship", relationship, body.rstrip("。")),
            update("sponsor_name", name, name),
        ],
    )

    assert result.updates == []
    assert not result.requires_human_review


def test_old_self_funding_does_not_block_new_explicit_sponsor_correction():
    result = validate("Changed plans: my sister will pay for my trip.", [
        update("sponsor_relationship", "sister", "my sister"),
        update("funding_source", "personal_sponsor", "my sister will pay for my trip"),
    ], known={"funding_source": "self"})
    assert {item.field for item in result.updates} == {"sponsor_relationship", "funding_source"}
    assert not result.requires_human_review


@pytest.mark.parametrize("fault", ["confidence", "invented_excerpt", "conflict"])
def test_real_validation_failures_keep_existing_review_semantics(fault):
    body = "My sponsor is my sister. My sponsor is my mother."
    candidates = [update("sponsor_relationship", "sister", "My sponsor is my sister.")]
    if fault == "confidence":
        candidates[0].confidence = .2
    elif fault == "invented_excerpt":
        candidates[0].source_excerpt = "An invented source"
    else:
        candidates.append(update("sponsor_relationship", "mother", "My sponsor is my mother."))
    result = validate(body, candidates)
    assert result.updates == [] and result.requires_human_review and result.ambiguities


@pytest.mark.parametrize("body", [
    "这次费用由父母资助。",
    "我的旅行费用由父母支付。",
    "我这次的旅行费用将由我的父母承担。",
    "本次赴英旅费全部由我父母承担。",
    "这次费用由爸妈共同资助。",
    "我的父母资助我。",
    "My parents will pay for my trip.",
    "My parents are my sponsors.",
])
def test_explicit_parents_funding_for_current_applicant_is_retained(body):
    candidate = update("sponsor_relationship", "parents", body)
    result = validate(body, [candidate])
    assert result.updates == [candidate]
    assert not result.requires_human_review and result.ambiguities == []


@pytest.mark.parametrize("body", [
    "这次父母只负责接待我，旅行费用我自己支付。",
    "My parents will host me. I will pay my own travel costs.",
    "这次我朋友的费用由父母资助。",
    "这次费用由我朋友的父母资助。",
    "这次我的费用由朋友的父母资助。",
    "我朋友这次去英国的费用由父母资助，我自己的旅费自己承担。",
    "这次费用由父母资助我朋友。",
    "父母承担我朋友的旅行费用。",
    "My parents will pay for my friend's trip.",
    "如果这次费用由父母资助，我再找他们要证明。",
    "若这次费用由父母资助，我再找他们要证明。",
    "If my parents pay for my trip, I will ask for a letter.",
    "这次费用不是由父母资助。",
    "这次费用不由父母资助。",
    "这次费用并非由父母资助。",
    "父母不资助我。",
    "My parents are not my sponsors.",
    "My parents will not pay for my trip.",
    "邮件示例写着‘这次费用由父母资助’，但我这次不由父母资助。",
    "收到。\n\nOn Friday, Adviser wrote:\n这次费用由父母资助。",
])
def test_parents_host_third_party_conditional_negative_or_quoted_role_is_not_funding(body):
    candidate = update("sponsor_relationship", "parents", body)
    result = validate(body, [candidate])
    assert result.updates == []
    assert not result.requires_human_review
    asked = validate(body, [candidate], known={"funding_source": "personal_sponsor"},
                     requested=["sponsor_relationship"])
    assert asked.updates == [] and not asked.requires_human_review


def test_parents_role_is_not_implied_by_a_proposed_or_known_funding_enum():
    body = "这次父母接待我。"
    relation = update("sponsor_relationship", "parents", "父母")
    proposed = validate(body, [
        update("funding_source", "personal_sponsor", body), relation,
    ])
    assert not any(item.field.startswith("sponsor_") for item in proposed.updates)
    known = validate(body, [relation], known={"funding_source": "personal_sponsor"})
    assert known.updates == [] and not known.requires_human_review


@pytest.mark.parametrize("body", ["父母", "我父母", "我的父母", "My parents"])
def test_parents_short_answer_uses_only_a_sent_sponsor_question(body):
    candidate = update("sponsor_relationship", "parents", body)
    result = validate(body, [candidate], known={"funding_source": "personal_sponsor"},
                      requested=["sponsor_relationship"])
    assert result.updates == [candidate] and not result.requires_human_review
    assert validate(body, [candidate], known={"funding_source": "personal_sponsor"}).updates == []
    assert validate(body, [candidate], requested=["sponsor_relationship"]).updates == []


def test_combined_parent_identity_answer_uses_the_sent_pair_and_retains_all_three_facts():
    body = "是父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国。"
    candidates = [
        update("sponsor_relationship", "parents", "父母两位共同资助"),
        update("sponsor_name", "陈国强、李美兰", "父亲陈国强、母亲李美兰"),
        update("sponsor_is_in_uk", False, "他们都不住在英国"),
    ]

    result = validate(
        body,
        candidates,
        known={"funding_source": "personal_sponsor"},
        requested=["sponsor_relationship", "sponsor_name"],
    )

    assert [(item.field, item.value) for item in result.updates] == [
        ("sponsor_relationship", "parents"),
        ("sponsor_name", "陈国强、李美兰"),
        ("sponsor_is_in_uk", False),
    ]
    assert not result.requires_human_review and result.ambiguities == []


@pytest.mark.parametrize(
    ("body", "relationship", "name", "relationship_excerpt", "location_excerpt"),
    [
        ("是我父亲陈建国资助，他不在英国。", "father", "陈建国", "我父亲", "他不在英国"),
        (
            "My father Jian Chen is sponsoring my trip and he doesn't live in the UK.",
            "father", "Jian Chen", "My father", "he doesn't live in the UK",
        ),
    ],
)
def test_single_sponsor_identity_and_location_answer_uses_the_sent_combined_question(
    body, relationship, name, relationship_excerpt, location_excerpt,
):
    result = validate(
        body,
        [
            update("sponsor_relationship", relationship, relationship_excerpt),
            update("sponsor_name", name, name),
            update("sponsor_is_in_uk", False, location_excerpt),
        ],
        known={"funding_source": "personal_sponsor"},
        requested=["sponsor_relationship", "sponsor_name"],
    )

    assert [(item.field, item.value) for item in result.updates] == [
        ("sponsor_relationship", relationship),
        ("sponsor_name", name),
        ("sponsor_is_in_uk", False),
    ]
    assert not result.requires_human_review and result.ambiguities == []


@pytest.mark.parametrize("body", [
    "如果是我父亲陈建国资助，他不在英国。",
    "我朋友说‘是我父亲陈建国资助，他不在英国’。",
    "My friend said my father Jian Chen is sponsoring my trip and he doesn't live in the UK.",
    "If my father Jian Chen sponsors my trip, he will not live in the UK.",
])
def test_single_sponsor_answer_does_not_borrow_conditional_quoted_or_reported_context(body):
    result = validate(
        body,
        [
            update("sponsor_relationship", "father", "我父亲" if "父亲" in body else "my father"),
            update("sponsor_name", "陈建国" if "陈建国" in body else "Jian Chen",
                   "陈建国" if "陈建国" in body else "Jian Chen"),
        ],
        known={"funding_source": "personal_sponsor"},
        requested=["sponsor_relationship", "sponsor_name"],
    )

    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("body", "relationship_excerpt", "name_excerpt", "location_excerpt"),
    [
        (
            "如果是父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国。",
            "父母两位共同资助", "父亲陈国强、母亲李美兰", "他们都不住在英国",
        ),
        (
            "我朋友说‘是父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国’。",
            "父母两位共同资助", "父亲陈国强、母亲李美兰", "他们都不住在英国",
        ),
        (
            "是我朋友的父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国。",
            "父母两位共同资助", "父亲陈国强、母亲李美兰", "他们都不住在英国",
        ),
        (
            "父母两位共同接待我，父亲陈国强、母亲李美兰，他们都不住在英国。",
            "父母两位共同接待", "父亲陈国强、母亲李美兰", "他们都不住在英国",
        ),
    ],
)
def test_combined_parent_answer_does_not_borrow_hypothetical_quoted_other_or_host_context(
    body, relationship_excerpt, name_excerpt, location_excerpt,
):
    result = validate(
        body,
        [
            update("sponsor_relationship", "parents", relationship_excerpt),
            update("sponsor_name", "陈国强、李美兰", name_excerpt),
            update("sponsor_is_in_uk", False, location_excerpt),
        ],
        known={"funding_source": "personal_sponsor"},
        requested=["sponsor_relationship", "sponsor_name"],
    )

    assert result.updates == []
    assert not result.requires_human_review


@pytest.mark.parametrize(
    ("known", "requested"),
    [
        ({}, ["sponsor_relationship", "sponsor_name"]),
        ({"funding_source": "self"}, ["sponsor_relationship", "sponsor_name"]),
        ({"funding_source": "personal_sponsor"}, []),
        ({"funding_source": "personal_sponsor"}, ["sponsor_relationship"]),
    ],
)
def test_combined_parent_answer_requires_known_personal_funding_and_both_sent_fields(
    known, requested,
):
    body = "是父母两位共同资助，父亲陈国强、母亲李美兰，他们都不住在英国。"
    result = validate(
        body,
        [
            update("sponsor_relationship", "parents", "父母两位共同资助"),
            update("sponsor_name", "陈国强、李美兰", "父亲陈国强、母亲李美兰"),
            update("sponsor_is_in_uk", False, "他们都不住在英国"),
        ],
        known=known,
        requested=requested,
    )

    assert result.updates == []
    assert not result.requires_human_review


def test_parents_passive_funding_does_not_make_the_separate_host_a_sponsor():
    body = "我是学生，这次费用由父母资助。去英国探望姐姐，住她家。"
    result = validate(body, [
        update("funding_source", "personal_sponsor", "这次费用由父母资助"),
        update("sponsor_relationship", "parents", "父母"),
        update("sponsor_is_in_uk", True, "去英国探望姐姐"),
    ])
    assert {(item.field, item.value) for item in result.updates} == {
        ("funding_source", "personal_sponsor"), ("sponsor_relationship", "parents"),
    }
    assert not result.requires_human_review and result.ambiguities == []


def test_current_parents_correction_keeps_the_original_narrow_source_excerpt():
    body = "我现在工作了，但这次费用由父母资助。"
    candidate = update("sponsor_relationship", "parents", "费用由父母资助")
    result = validate(body, [candidate])
    assert result.updates == [candidate] and not result.requires_human_review
