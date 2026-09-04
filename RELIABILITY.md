# Reliability design

Reliability is a property of the control plane, not a claim about prompt quality.

## Responsibility boundary

AI may propose a `CasePatch`, classify a document, extract candidate facts with evidence locations,
and render an already-decided message plan. Pydantic rejects unknown fields and malformed values.
The deterministic application layer owns requirements, issue resolution, state transitions, gate
checks, persistence, outbound side effects, and pack generation.

The same extraction call may propose `customer_questions`: at most four enum topics with a
current-message excerpt and confidence. They are not free-form answers, sources, facts, tool
calls or permission to advance. Supported topics select dated, reviewed GOV.UK wording; a
document-list topic selects the existing case-specific requirements. Invalid/quoted/declined
intents are dropped independently of otherwise valid facts. Current-turn topics reset on the
next event; the durable applicant profile and sent-question ledger remain separate.

This follows the constrained-data-flow principle in the
[OpenAI agent safety guide](https://developers.openai.com/api/docs/guides/agent-builder-safety#use-structured-outputs-to-constrain-data-flow).
The shared contract is used by both provider adapters, but DeepSeek uses JSON mode followed by
Pydantic validation, not an API-enforced strict-schema guarantee. Literal excerpt matching and
valid enums do not prove correct intent or a complete answer. Model self-reported confidence is
only a threshold, not calibrated accuracy; classification, sender invariants and human reading
are evaluated separately. Reviewed wording can still be irrelevant if a model misclassifies.

## Invariants

Customer-requested preparation pause/restart now has a separate persisted control and outbox
epoch; see [the contract and evaluation plan](PREPARATION_CONTROL.md). It is not a human-review
override or confirmation. Undecided dates remain a narrower question deferral.

- An inbound provider message ID is an idempotency key.
- Attachments are deduplicated by SHA-256.
- Corrected documents and evidence are marked superseded rather than erased.
- Critical final facts have active provenance entries.
- Workflow transitions follow an allow-list.
- Open blockers, stale policy, out-of-scope routes, missing confirmation, or missing provenance prevent
  readiness.
- A duplicate event creates no duplicate case mutation, reply, document, or delivery.
- Attachment text is untrusted data and has no tool or transition authority.
- Outbound replies are claimed transactionally before provider delivery so concurrent workers cannot
  send the same pending row.
- Each outbox row owns its channel, recipient, and external thread; a channel worker cannot claim a
  different provider's message.
- Authenticated webhook events enter a durable, channel-isolated queue before Agent work. Processing
  uses expiring leases, finite retries, idempotent replay, visible dead letters, and clears the queued
  raw payload after success.
- Retryable and permanent provider failures have distinct persisted states; retries are finite and
  use exponential backoff.
- A claimed send with no recorded result is reconciled by deterministic RFC Message-ID; a definite
  no-match becomes `AMBIGUOUS` and cannot be retried without an explicit operator decision.
- Every model adapter is wrapped by one patch guard: candidate facts need an allowed field, an exact
  inbound excerpt, a valid field value, sufficient confidence, and no conflicting candidate.
- Extraction retries are bounded to two attempts and then abstain to human review. Drafting failure,
  empty/oversized text, and prohibited outcome claims use deterministic customer wording.
- `awaiting_confirmation` is selected only when final confirmation is the sole failed gate. Missing
  facts, documents, scope checks, provenance, dates, or policy freshness remain `blocked`.
- Applicant replies are rejected if they omit an open blocker or required item, claim pack release
  before confirmation, omit the exact confirmation statement, retain a name placeholder, or omit the
  human-review boundary after release.
- The provider receives the email body inside an explicit untrusted-data envelope. Proposed facts
  still require schema validity, an exact source excerpt, an allowed field and a valid type before
  mutation; common relationship wording is canonicalised only after those checks.

## Failure behaviour

| Failure | Deterministic behaviour |
|---|---|
| LLM unavailable/refuses | No patch is applied; queue a bounded fallback or human review |
| Schema invalid | One constrained retry in live orchestration, then targeted clarification/review |
| Candidate fact is ungrounded/invalid/conflicting | Reject it before mutation and require human review |
| Model drafts an approval/submission claim | Discard it and use the deterministic non-advisory reply |
| PDF unreadable or wrong type | Mark replacement required; do not extract guessed facts |
| Low-confidence critical fact | Keep unresolved and ask the applicant to confirm |
| Duplicate event | Return existing state without a new outbox row |
| Channel transient failure | Persist `RETRY`, increment attempts, and use finite exponential backoff |
| Channel permanent failure | Persist `FAILED`, stop automatic retry, and expose the failure |
| Worker stops after claiming | Leave `SENDING` for provider reconciliation; never silently resend |
| Reconciliation finds provider copy | Mark `SENT` using the provider message ID without resending |
| Reconciliation finds no provider copy | Mark `AMBIGUOUS`; require an explicit operator retry decision |
| Sent-evidence query lacks authorization/access | Keep `SENDING`, restore access and query again; never resend |
| Token replacement fails before commit | Keep previous token intact; explicit reconnection verifies mailbox before replacement |
| Inbound email cannot be parsed | Store one redacted failure record by provider ID; do not mutate a case |
| Webhook worker crashes after claim | Lease expires; replay is safe through provider-event idempotency |
| Inbound processing repeatedly fails | Finite backoff, then visible `FAILED` queue item |
| Policy stale | Continue intake but block the delivery gate |
| Unsupported/sensitive case | Move to `HUMAN_REVIEW_REQUIRED` |

## Local data controls

The Docker review app listens on loopback only. Reviewers can export the canonical case, outbound
messages, failures, and delivery ledger as JSON. Raw processed inbound messages are intentionally
not retained. Case deletion needs both a visible browser confirmation and the exact case ID in the
request header; it deletes database rows, pending events for the same thread, and only that case's
derived directory and ZIP inside the configured output root.

## Final archive integrity

Before attaching a final ZIP, the sender requires a matching delivery-registry path and verifies
the SHA-256 of the exact bytes passed to the provider. It does not reopen the path after checking.
The case-pack download endpoint uses the same registry/hash requirement, returns the verified
bytes directly with `Cache-Control: no-store`, and rejects mismatches with HTTP 409. Historical
files are not deleted or regenerated just to make a failing check pass.

On 2026-09-04, three sender regressions initially returned SENT for modified file contents,
a missing delivery registry row and a different registered path (fake providers only). Those
tests and the corresponding three download checks now pass. Valid download tests also compare
the returned bytes to the generated archive. These checks assume a trusted local database;
they do not authenticate applicant documents, prove the initial pack is correct, protect against
simultaneous malicious modification of both registry and file, or recall a previously sent ZIP.
Downloads now hold the ZIP bytes in memory; large-package resource testing remains separate.

The guided-lab indicator/download now use the same current registry/revision/hash and held-update
boundaries, including returning already-verified bytes. Twelve isolated regression cases cover
tampering, missing/mismatched records, path escape, revoked confirmation and read failure. This is
fixture-lab integrity, not proof of authentic documents. Credential persistence, explicit
reauthorization and sent-evidence query recovery are documented in [GMAIL_RECOVERY.md](GMAIL_RECOVERY.md).

## Replay evidence

The assessment event log is a set of ordered `.eml` fixtures. `make demo` processes it, records
state/outbox/delivery counts, replays it in full, and fails if counts change. The audit directory
contains evidence, policy evaluations, the gate matrix, and the final structured snapshot.
## Reviewed delivery revisions

A finalized-case update does not silently rewrite a delivered attachment. Local operator review
checks the current snapshot and original archive integrity, preserves the old delivery and allocates
one new revision. Multiple retained updates require explicit batch authorization. Normal processing
is serialized in original receive order; an earlier failed/retrying update cannot be skipped.
All case-level summary confirmations are cleared and must be obtained again. Read
`HUMAN_REVIEW_RECOVERY.md` for commands, rollback behaviour and the still-missing reviewer UI/RBAC.

Outbox rows and delivery registries bind to `case_revision`. A stale pending message cannot attach
the latest pack merely because its case ID matches. Current-revision send attempts make that version
immutable; older attempted deliveries do not prevent a separately authorized new revision. Holds
block ready planning, generation, download and dispatch. Provider reconciliation remains read-only
with respect to a new revision: finding an old accepted send does not resend it or release new work.
Local tests cover these contracts, atomic batch rollback and exact-byte capture. They do not prove
real recipient redelivery or cancellation of a provider request already in flight.

## Conversation scope is not delivery authority

Question classification has explicit `off_topic` and `unsupported` alternatives. The former means
outside UK-visa preparation; the latter means a UK-visa question the reviewed answer set cannot
fully resolve. Neither supplies facts, consent, eligibility, free-form legal advice or a URL.
Distinct grounded boundary excerpts survive topic deduplication so downstream keyword fallback
does not reinterpret an excluded second question as a new visa fee or document-list request.

A narrow DRAFT scope-only response can preserve an unchanged SENT summary rather than replacing
it with a newly requested summary. It is unavailable for independent fact updates, attachments,
deferrals, existing blockers/held updates, review state, explicit or recognized natural confirmation,
or a request to continue preparation. Duplicate, sender and event-order checks still precede
extraction. Only the existing context-bound confirmation path may confirm a summary; no off-topic
classification grants that authority. Case excerpts reset each new event and are presentation data.

Real-provider failures and actual reply-level weaknesses are recorded in `CONVERSATION_REVIEW.md`.
Exact excerpts, confidence and typed fields still cannot prove semantic correctness. This boundary
reduces specific regressions without making the adviser a universal question-answering system.

### Controlled question-extraction comparison

The DeepSeek default now uses the measured neutral combined input wrapper. The explicit legacy
method preserves historical request behaviour for the benchmark; the question-only pass remains
experimental. Provider settings, fact schema, source/conflict guards and workflow/release authority
are unchanged. Full request-equivalence contracts prevent a benchmark arm from silently drifting
when the default changes. `capture_raw_responses` defaults to false; only the explicit fictional
probe enables it, and its latest-response fields clear before each corresponding request.

`adviser_reply_replay.py` rejects holdout, mismatched corpus/input/profile/expectations, incomplete
sources and existing output paths before replay. Saved neutral errors remain unavailable rather
than borrowing another arm's output. Replay uses no key or model, captures Gmail with network
disabled, records original/current fingerprints and does not report fresh classifier accuracy.
See [the experiment](QUESTION_UNDERSTANDING_EXPERIMENT.md) for retained failures and limitations.

### Case-aware informational steps

`Case.next_step_advice` is a reviewed presentation plan, not a model-provided permission. The
selector runs after validated changes and current gate evaluation. It neither changes facts nor
waives missing dates/documents, pause, review, current-summary consent or delivery requirements.
Only its actual missing question enters the existing SENT question ledger. A paused preview
never starts intake. Current summary checking precedes further document requests.

One next-item request cannot expand into a whole checklist by legacy keywords, while independent
checklist/FAQ requests remain visible. Guarded wording and the automatic Gmail sender retain the
selected step with the other answers. Captured sends, restarts and duplicates are tested locally;
the first real-model holdout still exposes semantic/scope failures. See
[the exact evidence and limitations](NEXT_STEP_ADVICE.md), not a general reliability percentage.
