# Application-record review: remaining implementation contract

Development design, **not an implemented approval flow or a release claim**.
Official source checked 2026-09-07:
[Standard Visitor application information](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa).

## What the guidance actually distinguishes

The application asks for the current home address and duration of residence,
parents' names/dates of birth if known, and annual earnings if the applicant has
income. Conditional information includes the past ten years' travel history,
employer address/phone, partner identity, payer name/address and UK family
name/address/passport number. These distinctions must not become a blanket request
for every contact's passport or a request to invent unknown parents' details.

The repository still lacks full coverage of those fields. This record feature
does not by itself close the original product scope or make the pack an official
completed application. Do not remove the remaining nonempty-record hold just
because descriptive fields or source identifiers are present.

## Required review boundaries

- Assess which conditional fields apply to each record and why, using case facts
  and the reviewed policy source. A contact is not automatically a relative,
  payer, partner or employer. Preserve those separate roles.
- Preserve partial travel dates as supplied. An approximate period can be useful
  for preparation but must not be silently converted to an exact application
  date or silently excluded from the ten-year window.
- Check every retained field/declaration/deferral against registered case events.
  Check contextual answer links against actually sent case-local question events.
  A missing source remains a review issue, not permission to invent a substitute.
- Record reviewer identity, substantive rationale, applicable-field decisions,
  policy version/review date and exact material snapshot. This must be a trusted
  operator action, never an LLM proposal or a customer confirmation inferred by
  the model. Stale snapshots reject the action atomically.
- Record changes invalidate applicability review and customer confirmations.
  Source-registration audit does not certify factual truth or document validity.
- Finalisation must still enforce processing consent, complete applicable facts,
  unresolved issues, document checks, human review and explicit final summary
  confirmation. An operator review alone must never send a pack.

## Evidence available now, and its limits

`workflow/record_source_audit.py` is a read-only prerequisite. It checks event
registration/case binding for current field sources, revisions, latest collection
declarations and active deferrals. It checks linked question registration, saved
question identifiers, SENT status and recipient/thread scope. It queries only
the selected question/case, not every customer's outbox.

Processed raw queue payloads are purged. The audit therefore **cannot independently
recompute a full original email's hash or reread its full content**. Literal
excerpts were checked during intake; retained excerpts/hashes and registered
events support traceability, not proof of truth or a fresh full-message review.
This limitation must be visible to reviewers rather than hidden behind a green
label. The audit currently neither persists a review decision nor replaces the
development hold; operator flow, source-bound decision invalidation and final
event-to-ZIP acceptance remain unfinished.
