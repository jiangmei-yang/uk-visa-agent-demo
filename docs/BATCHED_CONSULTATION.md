# Questions across separate emails — 2026-09-05

## The observed gap

With actual workflow/SQLite/automatic-sender code and a captured Gmail transport,
two ordinary emails in one intake cycle exposed a missing answer. The applicant
first asked where to apply, then how to translate Chinese supporting documents.
The last delivered reply answered translation only. A second experiment prepared
both drafts without sending, reopened the database, and processed a name update:
both earlier questions disappeared from the delivered reply.

These were four failing Chinese/English regressions, alongside two passing checks
that an already delivered answer was not repeated. The original source IDs were
processed successfully; a successful intake counter did not mean the questions
had been answered. No real mailbox or model API was used in these experiments.

## Implemented behavior

`Case.unsent_advice` records actual reviewed applicant questions independently of
`pending_advice`, which represents an omission/continuation notice. The former
needs evidence of an original applicant request, not an earlier SENT explanation.
The latter still needs its exact actually SENT notice. A status flag alone does
not invent either a customer request or an earlier promise.

- A later ordinary question or factual update can carry earlier unanswered FAQs
  into the newest reply. Fresh questions take priority; the combined reviewed FAQ
  set has a three-answer cap and a truthful omission notice for overflow.
- Original IDs, question context, qualifiers and source proposals remain bound to
  the request. A topic label alone does not collapse different bank-evidence
  questions or route conditions. A later answer is recompiled from the reviewed
  source set under the current source-review date, not copied from an old draft.
- A short request for a previously supplied application link retains its original
  SENT guidance context. It cannot borrow an unrelated future link as proof.
- A complete answer must be present in an actual SENT row before the request is
  consumed on a later processed event. An overflow item only moves to promised
  continuation after the exact omission notice was actually sent. A combined
  reply may bind that notice to a different event from the original question.
- PENDING/FAILED drafts are not answers. SENDING/AMBIGUOUS attempts are not
  definitely unsent; they remain for normal provider reconciliation, without
  duplicating their answer in another continuation email.
- Current no-link preferences are applied to both current and carried answers;
  the delivery attempt records that final version. Explicitly excluded topics,
  cancellation of earlier questions and an explicit route change stop automatic
  carry-over. Generic “continue” does not undo a cancellation. A fresh explicit
  question can be asked again without resuming preparation or confirming a summary.
- Old question bodies are never concatenated into a new extraction input. New
  facts/files still use ordinary intake and the current gate before reply merging.
  Old dates, profile-confirmation phrases and preparation instructions cannot be
  replayed as new facts or authority by consultation memory.

Quoted questions, reported speech and intentions to ask later do not create a
current unsent request. A real independent question after a quotation remains
answerable. These are bounded recognizers, not a claim to interpret all natural
language correctly.

## Evidence and corrections

The new tests use original ordinary question wording, actual database restarts,
actual reviewed automatic sender/dispatcher code, source-grounded synthetic model
proposals and captured transport. They check the final sent text and source links,
not only an internal plan or extraction count. Network access is disabled.

Development exposed additional failures, retained rather than reported as success:

- Six initial direct question-carry tests failed; first Gmail run was 4 failed /
  2 passed. Later direct checks caught cancellation being undone by “continue”,
  quoted/future questions being mistaken for requests and no-link preferences
  applying only to the new answer.
- First full regression: **52 failed / 3,399 passed in 52.15s**. In particular,
  `case.customer_answers` shared the current plan's mutable list; appending a
  dynamic next-step answer changed that plan too, causing the merge to drop it.
  Copying the plan's list preserved the existing next-step contract. Related
  scope/expiry regressions were corrected without weakening those tests.
- Two older continuation expectations were revised deliberately: an uncertain
  send must be reconciled instead of immediately repeating its answer; and an
  actual saved applicant question can be answered without claiming an omission
  notice was sent. A separate legacy-snapshot negative check still requires the
  exact SENT notice when no independent unsent-request record exists.

Reproduce the focused checks:

```sh
.venv/bin/pytest -o addopts='' -q \
  tests/integration/test_gmail_batched_consultation.py \
  tests/integration/test_unsent_advice.py \
  tests/integration/test_advice_continuation.py \
  tests/unit/test_advice_preferences.py
```

## Retention and limits

Unsent context includes the full original request, any supplied personal details,
source proposals, answer-identity text and attempted replies. It is private case
state, not model training. Old snapshots default the new list to empty; migration
does not manufacture requests from outbox drafts. Existing export includes the
state and deleting a case removes it from the active DB. There is no automatic
TTL for unanswered/deferred questions; remote messages, backups and physical
SQLite remnants are not erased by logical case deletion.

This is not a fresh DeepSeek accuracy run, a real recipient trial or an independent
naturalness score. Broader paraphrases, source refresh, uncoached conversations,
ordinary-document cross-source checks and recipient-side final ZIP acceptance
remain in the overall audit. Preferences are bounded current instructions, not a
general persistent preference-learning system. Unrecognized or changed source
variants remain unresolved rather than receiving a guessed answer.
