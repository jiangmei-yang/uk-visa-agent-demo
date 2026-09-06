# Personal sponsor live-model development experiment

## Scope

Real `deepseek-v4-flash` extraction, fictional people/addresses, persisted workflow
reopened each turn, actual outbox dispatch into a fictional-only capture adapter.
No Gmail API, real applicant material, approval, finalpack release or deployment.
Scenario date is explicitly frozen at 2026-09-06; report run timestamps are actual.
These are exposed development scenarios, not an independent accuracy score.

## Retained attempts

| Report | Actual calls | Tokens | Result |
| --- | ---: | ---: | --- |
| `eval_output/sponsor_address_2026-09-07-v1.json` | 6 | 32,650 | 2/6 checks passed |
| `eval_output/sponsor_address_2026-09-07-v2.json` | 7 | 38,578 | 6/7 checks passed |
| `eval_output/sponsor_address_2026-09-07-v3.json` | 7 | 38,629 | 7/7 checks passed |

Total **20 calls / 109,857 tokens**, no retries. The v1 role replay and v2 context
replay are explicitly offline diagnostics and add zero calls. No failed report
was overwritten or relabelled successful.

## Findings and fixes

1. V1's model correctly supplied the sponsor's name/relationship. A local broad
   hypothetical-word check rejected the literal surname “Example”. Hypothetical
   detection now requires framing such as “for example” or leading “Example:”,
   rather than matching a surname anywhere. Negative framing tests remain.
2. V1 also incorrectly assumed that an address question would be asked immediately
   after first-turn preparation advice. With valid sponsor identity, the existing
   pacing offered the preparation action first. V2 explicitly asks “What is the
   next step?” before answering the resulting address question. The unchanged V1
   report preserves the original experiment design error; it is not evidence that
   the agent should attach an unsolicited “I need to check” to an unasked question.
3. “Father is paying instead of my mother” was accepted for name but rejected for
   relationship because both relatives were counted. A bounded affirmative payment
   contrast now excludes the displaced relative from role matching, retaining the
   original source excerpt and rejecting proposals that select the displaced role.
4. V2 correctly sent the new sponsor's address question, but the model emitted no
   address update for the short answer. The offline diagnostic exposed both the
   old pending applicant-name question and the current address question in the
   model's requested-field list. Verified latest address/duration questions now
   supply only that field as the current extraction context; older tasks remain
   in case memory. The prompt also explicitly describes this trusted short-answer
   context. V3 succeeded; this does not prove that the stale context was the sole
   cause of a stochastic model omission.

The probe now stores its exact extraction event alongside raw model output, and
rejects replay when the saved scenario contract differs. It cannot silently use
V1 proposals for V2's changed customer-message sequence. Production source and
probe hashes remain in each report; no historical hashes are rewritten.

## Checks and manual reading

The tests check exact expected fields, current address evidence source, required
question actually sent, uncertainty persistence, no reasking of deferred dates,
no fabricated travel dates, one main question, no unexpected human-review hold,
no unrequested release, one captured send and SQLite state equality.

Targeted role/address/residence tests: **156 passed** (1.50s), Ruff and strict Mypy
passed (92 modules). Two additional no-network tests replay V3 through the real
workflow and verify rejection of V1 under the changed scenario contract; **2
passed** (0.40s).

The development-wide run passed **5,011 tests / 2 deselected**, 101.60s with one
existing Starlette warning. It was collected before those two new replay tests,
which were run separately as reported above. The excluded consultant/financial
source-bound reports remain stale; this sponsor experiment does not replace them.

All seven V3 customer replies were read. They contain useful sponsor preparation
and official links, retain date uncertainty, defer the missing address, update the
new payer and accept the later Chinese correction. Remaining editorial defects:
the opening repeats too many profile facts, “I've got the starting point” is reused
mid-journey, and replacement acknowledgement contains machine-like field labels.
Passing this scenario does not establish consultant-like naturalness or broad
language robustness. Independent unscripted journeys, multi-payer completeness,
remaining application fields, finalpack QA, operator UX, real Gmail and GitHub
release acceptance remain open.
