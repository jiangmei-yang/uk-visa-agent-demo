# Application-record review: remaining implementation contract

Development contract, **not a release claim**. The trusted local transaction is
now implemented, with an explicit local-terminal command entry point described in
`OPERATOR_RECORD_REVIEW.md`. A guided authenticated UI and remaining broader
application-field coverage are not complete.
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
completed application. Descriptive fields or source identifiers alone cannot
satisfy the nonempty-record review gate described below.

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
  Supplementing an existing contact's phone/passport/support details can preserve
  the original exhaustive-list declaration, but never preserves its old review
  approval. Identity, location or membership changes still invalidate list scope.
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
label. The audit itself neither persists a review decision nor grants release.

## Implemented local operator transaction

`workflow/record_review.py::review_application_records` checks an exact saved-case
fingerprint under an atomic write lock, current processing permission/policy,
active draft state, held updates, complete collection intake and registered
sources. It requires one substantive assessment for each current record and an
explicit travel-scope check where travel records exist. UK contacts must be
classified; known family relationship labels cannot be treated as ordinary
contacts, and a family classification requires supplied passport information.
This is bounded guard logic around a human decision, not universal relationship
understanding. The function asserts an operator name; callers must authenticate
the operator and own the service state lock. It is not a public/model endpoint.

The saved decision and its history bind profile, record ledger, documents,
evidence content, collection-question links, preparation/revision state and full
policy content. Material changes make the old decision stale. Customer evidence
confirmation flags are deliberately excluded from this binding to avoid an
endless review-confirm-review loop. The transaction clears prior profile/final
confirmation context and does not create any outbox message or delivery.

The nonempty-record gate now requires a matching saved review instead of an
always-false development placeholder. Final-ready workflow and pack generation
also check source registration; pack reuse cannot bypass the latter. A captured
Gmail-protocol journey with fictional documents now reaches ZIP only after a
review and fresh SENT profile/final confirmations. This is local integration
evidence, not an authenticated real human review, visual PDF audit, live model
test or real mailbox recipient acceptance.

Known-family passport intake now provides conditional EN/ZH advice and a
field-specific uncertainty deferral. This bounded behavior does not implement all
relationship wording or conditional applicability exceptions.

Still required: guided operator interface, complete missing-applicable-detail feedback to
the customer, broader conditional field intake, the
broader home/parent/employer/partner/payer fields, independently checked output
quality and a fresh source-bound live-provider/Gmail acceptance run.
