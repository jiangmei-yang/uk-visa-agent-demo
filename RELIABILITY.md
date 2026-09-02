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
- Retryable and permanent provider failures have distinct persisted states; retries are finite and
  use exponential backoff.

## Failure behaviour

| Failure | Deterministic behaviour |
|---|---|
| LLM unavailable/refuses | No patch is applied; queue a bounded fallback or human review |
| Schema invalid | One constrained retry in live orchestration, then targeted clarification/review |
| PDF unreadable or wrong type | Mark replacement required; do not extract guessed facts |
| Low-confidence critical fact | Keep unresolved and ask the applicant to confirm |
| Duplicate event | Return existing state without a new outbox row |
| Channel transient failure | Persist `RETRY`, increment attempts, and use finite exponential backoff |
| Channel permanent failure | Persist `FAILED`, stop automatic retry, and expose the failure |
| Worker stops after claiming | Leave `SENDING` as an ambiguous state for reconciliation; never silently resend |
| Policy stale | Continue intake but block the delivery gate |
| Unsupported/sensitive case | Move to `HUMAN_REVIEW_REQUIRED` |

## Replay and audit

The assessment event log is a set of ordered `.eml` fixtures. `make demo` processes it, records
state/outbox/delivery counts, replays it in full, and fails if counts change. The audit directory
contains evidence, policy evaluations, the gate matrix, and the final structured snapshot.
