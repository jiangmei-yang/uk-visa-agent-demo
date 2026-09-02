# Reliability design

Reliability is a property of the control plane, not a claim about prompt quality.

## Responsibility boundary

AI may propose a `CasePatch`, classify a document, extract candidate facts with evidence locations,
and render an already-decided message plan. Pydantic rejects unknown fields and malformed values.
The deterministic application layer owns requirements, issue resolution, state transitions, gate
checks, persistence, outbound side effects, and pack generation.

## Invariants

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
- Retryable and permanent provider failures have distinct persisted states; retries are finite and
  use exponential backoff.
- A claimed send with no recorded result is reconciled by deterministic RFC Message-ID; a definite
  no-match becomes `AMBIGUOUS` and cannot be retried without an explicit operator decision.
- Every model adapter is wrapped by one patch guard: candidate facts need an allowed field, an exact
  inbound excerpt, a valid field value, sufficient confidence, and no conflicting candidate.
- Extraction retries are bounded to two attempts and then abstain to human review. Drafting failure,
  empty/oversized text, and prohibited outcome claims use deterministic customer wording.

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
| Inbound email cannot be parsed | Store one redacted failure record by provider ID; do not mutate a case |
| Policy stale | Continue intake but block the delivery gate |
| Unsupported/sensitive case | Move to `HUMAN_REVIEW_REQUIRED` |

## Replay and audit

The assessment event log is a set of ordered `.eml` fixtures. `make demo` processes it, records
state/outbox/delivery counts, replays it in full, and fails if counts change. The audit directory
contains evidence, policy evaluations, the gate matrix, and the final structured snapshot.
