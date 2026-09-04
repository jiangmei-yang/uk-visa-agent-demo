# Mixed permission and consultation — 2026-09-05

This repair closes a concrete consultation gap: a customer should not lose a
question, new fact or attachment merely because it shares an email with their
processing agreement. It is not a general naturalness score or a legal-compliance
certification. The previous [consultation and obstacle repairs](ADVICE_CONTINUATION.md)
remain separate evidence.

## Execution contract

- A grant still requires the exact public reference from the current actually
  SENT notice. Copied/quoted speech, conditional or restricted agreement, an
  old reference and a grant posed as a question do not supply permission.
- The first scan records control only, plus a hash and attachment-presence bit.
  It does not decode attachments, inspect their names, call a model or store a
  full ordinary body before permission. Gmail's raw MIME bytes do enter RAM;
  this is not a claim that the process never receives those bytes.
- Independent facts/questions/files remain under the original provider ID,
  receipt time, subject and RFC reply metadata. No fabricated customer follow-up
  or retimestamping is used. Gmail receipt precision is milliseconds.
- After all discovered controls are scanned, a later cycle refetches the same
  original. Only the verified grant clause is removed from the business view.
  The original metadata and source remain available for normal evidence/holding.
- Reprocessing the original checks its latest-body/envelope hash, attachment
  presence and current authorization. This consent hash is not an attachment-byte
  integrity check; attachment integrity still belongs to normal document intake.
  It does not increment the grant epoch again. Withdrawal and new-scope gates
  still apply. Pure grant replies remain controls, not empty business turns.
- A mixed grant cannot also resume paused preparation or confirm a profile/final
  summary. An explicit pause can still be honored. Granting processing is not
  authority to send a pack or submit an application.
- A supported operator review retry retains its exact `review_actions` link to
  the original held message and grant audit. Its operator retry ID is not a new
  customer consent. The original hash/body and current authorization are checked;
  resume/confirmation cannot reappear through that retry.
- When a notice changes between scans, waiting mixed originals can trigger the
  new notice without business processing. A previous refusal/withdrawal does not
  automatically trigger another request. A reviewed retry waiting for consent
  retains the original Gmail transport even though its queue is `gmail_review`;
  resuming that special queue requires the exact durable review/held binding.

Scope migration compares the persisted scope hash with the new notice hash.
The actual notice text changing requires fresh authorization even if the
provider/model/version labels happen to match. Old audit rows default to
control-only; migration does not invent business processing permission.

## Test evidence

The new suites use isolated SQLite stores, actual workflow/queue/dispatcher
implementations, synthetic extraction proposals and captured Gmail senders.
Network access is prohibited. The successful FAQ examples pass through the
reviewed automatic sender to actual captured `SENT` rows, checking an official
application link and the flight-booking question in the delivered reply. This
is stronger than checking an internal plan, but **not** real mailbox delivery or
a fresh DeepSeek evaluation.

Additional checks cover facts before or after the grant, same-clause punctuation,
attachment-only grants, restart/duplicate handling, same-ID tampering, pending
controls beyond the first 100 messages, late withdrawal, old schema defaults,
scope changes and real operator review/worker recovery paths.

The automatic sender now withholds obsolete, unattempted control drafts before
claiming a provider-send slot, using the same latest-row rule already enforced
at send time. This keeps an old grant receipt from delaying the current business
reply by another poll cycle when their creation times straddle a second. Current
notices and SENDING/AMBIGUOUS/SENT evidence are not discarded.

Reproduce:

```sh
.venv/bin/pytest -o addopts='' -q \
  tests/unit/test_mixed_consent_boundaries.py \
  tests/integration/test_mixed_processing_consent.py \
  tests/integration/test_gmail_mixed_processing_consent.py
```

Failures found during development are not discarded: the first core run had two
failures because trimming business punctuation broke exact source excerpts.
The first full run had four Gmail fixture assertions comparing microseconds to
Gmail's actual millisecond receipt precision (3,250 other tests passed). The
independent parser audit first exposed seven failures, then two more nearby
over/under-restrictions. A later review caught authorization ending in a question
mark. Real review-queue tests exposed old-reference reclassification and the
`gmail_review` queue versus original `gmail` channel recovery mismatch. A full
run during those additions recorded 2 failed / 3,316 passed; final results are
recorded separately in VALIDATION.md, not substituted for these intermediate runs.
One of those two failures exposed the old-receipt slot delay above; its timing
condition was made deterministic rather than deleting the no-extra-send check.

## Remaining limitations

Out-of-order originals remain held; existing review APIs reject stale chronology.
This repair does not change dates or pretend an unsupported chronology-review
operation exists. Multiple separate emails discovered in one batch may supersede
earlier unsent FAQ replies; broader batch question retention remains work.

Consent phrasing is a bounded deterministic recognizer, not a trained legal
consent classifier. Independent notice usability, a real applicant grant,
ordinary-material recipient-side final ZIP, broader source cross-checks and
uncoached user evaluation are still outstanding. WhatsApp's pre-download consent
flow has not been established by these Gmail tests. No real customer data, paid
model call or manual real mailbox send was used for this repair.
