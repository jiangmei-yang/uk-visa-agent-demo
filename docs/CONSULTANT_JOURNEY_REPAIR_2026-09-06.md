# Multi-turn consultant repair, 2026-09-06

This is development evidence, not a perfect-agent claim, blind evaluation, or
recipient-side Gmail acceptance. The reviewed reply composer remains code-owned;
DeepSeek proposes structured facts. We did not train or fine-tune a model.

## Retained failure

`eval_output/consultant_journey_2026-09-06-v1.json` retains all 12 fictional
DeepSeek extraction attempts, raw proposals and captured replies. The original
checker reported 2/12 passing turns. It also had one **test bug**: Python `date`
versus JSON string comparison incorrectly failed the correctly stored birthday
on student turn 2. That original report is unchanged. With only that comparison
corrected, the original results are 3/12, not 2/12.

Other failures were substantive: correct student/purpose/funding/location
proposals were rejected; the school-record follow-up received an empty
acknowledgement; self-employed income and own-account transfer questions were
not answered; a direct English pause was discarded.

## Repair and boundaries

- Accept the explicitly supported natural wording while retaining exact excerpt,
  subject ownership, uncertainty, negation and conditional guards. Tests also
  exposed and repaired translation/reported-speech and negative-payer leaks.
- Keep school-record help across an actually SENT discussion and database reopen.
  Declining a repeat of application steps does not decline the school-file answer.
- Give self-employed income-record suggestions and distinguish own-account
  transfers from new earnings. No actual transaction is classified, no income is
  summed, no tax determination or funding-sufficiency decision is made.
- Answer a direct guarantee request honestly rather than ignoring it. These
  replies do not confirm an application, grant consent or release documents.
- Honour brief pacing by omitting an unsolicited process overview, not by
  truncating a reviewed answer. Recognise a whole-clause current pause without
  granting contextual resume or additional authority.
- Use an explicit parent-payer wording hint to ask the parent's name rather than
  immediately asking who pays again. This hint is not a persisted relationship
  fact; broader relationship extraction and later-turn follow-up remain open.

The financial and non-guarantee explanations were checked against the
[GOV.UK supporting-documents guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
on September 6. Matching account transfers is a practical source-of-funds
explanation, not a UKVI-mandated document checklist or acceptance promise.

## Reproduction

```sh
.venv/bin/pytest tests/integration/test_consultant_journey_replay.py tests/unit/test_income_clarification.py
.venv/bin/python scripts/consultant_journey_probe.py --replay eval_output/consultant_journey_2026-09-06-v1.json --output /tmp/new-offline-journey.json
# Paid, 12 fictional extractions, no retries, no mailbox/network sends:
.venv/bin/python scripts/consultant_journey_probe.py --allow-model-calls --output eval_output/new-provider-journey.json
```

Output paths must not exist. Offline replay never reads the API secret or makes
provider calls. Each turn uses the real guarded workflow, SQLite reopen,
automatic reply sender and outbox dispatcher with a fictional capture transport.
The v2 checker fixes the date comparison and strengthens the sponsor action check.
Keyword, pacing and fact assertions remain proxies, not human naturalness scores.

The second live run (`consultant_journey_2026-09-06-v2.json`) reported 12/12,
but manual reading rejected that as full acceptance: the student opener merely
acknowledged studying (matching a weak keyword assertion), and the guarantee reply
contained a redundant generic unsupported-answer preface. The report is retained
unchanged. Contract v3 explicitly requires a school-document preparation action
and forbids that redundant preface. Both retained proposal sets are replayed with
the stronger current contract; later passing runs do not erase either limitation.

## Still not complete

The new wording is a bounded repair, not general language understanding in the
post-validator. Diverse paraphrases, independent uncoached users, ordinary mixed
documents, cross-email context and real transport recovery still require broader
acceptance. Source-bound PDF extraction is separately retested before release;
the prior financial report is not silently relabelled as current evidence.

Manual review still identifies conversational polish to do: brief preferences
currently apply to the current reply, not a durable customer style preference;
the student opener offers a pair of useful documents when the customer asked for
one thing; later unsolicited process descriptions and administrative introductions
can still be longer than necessary. These are not scored as "perfect" merely
because the strengthened automated checks passed.

## Latest real-model evidence

Contract-v3 run: **12/12 turns**, zero extraction fallback, every turn persisted
across reopening and captured exactly once as SENT. Source-bound four-document
run v12: **4/4**. Both bind source commit `d5fda038c63891338c38a57394de8e69294a27a6`.
The 80 source files and journey probe are checked against the v3 manifest in tests;
the financial test separately checks all 80 source files, its probe, prompt/schema
and frozen PDF hashes. This is a fresh model run, not a re-score of old responses.

All three journey runs are retained: 36 extraction calls, 134,325 provider-reported
tokens (129,293 input, 5,032 output). Two financial refresh runs added eight calls
and 17,488 tokens. No mailbox calls, real applicant documents or retries were used.
Four existing one-page fictional PDFs were rendered with `pdftoppm` and inspected;
their fictional labels and values were legible, with no edits to the frozen PDFs.

The fresh disposable HTTP check passed all **12 checks**, including duplicate and
out-of-order requests, confirmation gates, ZIP integrity, repeat-download identity
and exclusion of internal audit files. The generated ZIP hash stayed
`69508420e47d5f843bf89eeb088347fa1d0e1cb2252d91c8108fb98bbdb18582`.

- [Current journey report](../eval_output/consultant_journey_2026-09-06-v3.json)
- [Current PDF extraction report](../eval_output/financial_document_deepseek_2026-09-06-v12.json)
- [Fresh HTTP acceptance](../eval_output/consultant_journey_release_smoke_2026-09-06.json)

## Local regression and deployment

- Full suite: **4,525 passed in 91.64s**, one existing Starlette/httpx deprecation
  warning; no source-binding test excluded. Ruff and strict Mypy passed (80 files).
- Focused saved-journey, preference and financial replay: **63 passed in 1.24s**.
- Docker is healthy; all 80 runtime source hashes match the workspace. Manifest
  digest: `e78c4b49419281197a2fe0191ec4389fbbda1673c1ec74e6c72cec9cb148745b`.
- Existing runtime volume was reused. The original ZIP stayed
  `8bc0681a837437d30675da7650cd9c61e1ecc69532ea969c77ceb383c458f724`.
- Existing Gmail worker reloaded as PID 52199 and was idle at
  `2026-09-06T05:12:09.017948+00:00`, with zero error-log lines. The database dump
  digest before/after was identical:
  `e50176d0c1de0e690a1f37a1c81ad9d56b45eb01241fe359a74bc28bfadec900`.
  Its one case, nine processed events, nine SENT rows and zero deliveries were
  preserved. No applicant consent, case fact or outbound mail was fabricated.
