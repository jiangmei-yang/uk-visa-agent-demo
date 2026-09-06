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

## Still not complete

The new wording is a bounded repair, not general language understanding in the
post-validator. Diverse paraphrases, independent uncoached users, ordinary mixed
documents, cross-email context and real transport recovery still require broader
acceptance. Source-bound PDF extraction is separately retested before release;
the prior financial report is not silently relabelled as current evidence.
