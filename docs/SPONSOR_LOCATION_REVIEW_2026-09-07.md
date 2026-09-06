# Sponsor location review — local implementation checkpoint

This is not a release acceptance or a claim of perfect adviser behaviour.

## Implemented

- Separate residence/current-presence observations retain their original event,
  source text, sponsor identity and identity epoch. Replacing a sponsor and later
  returning to the old name cannot revive the old decision.
- A local operator transaction requires the exact current case fingerprint,
  current policy, effective processing consent and registered source events.
  Paused/finalized cases, unreviewed held updates and uncertain sends are refused.
- Both dimensions must be explicit and nonconflicting for the current sponsor.
  Mixed residence/presence is not itself a contradiction. For this conservative
  demo implementation, either positive dimension keeps UK-status evidence required;
  both negative dimensions can be reviewed as not applicable. This is not proof
  of immigration status or eligibility.
- Review is stored with immutable metadata/history and binds source, identity,
  policy and preparation-control epoch. No legacy boolean is backfilled.
- Only the location hold is removed. Other review reasons remain. Profile/final
  confirmations are invalidated; review never sends email or creates a ZIP.
- The preparation planner no longer requires the ambiguous legacy location field
  after a current review. The release gate independently checks the policy-bound
  review, even if a caller has changed case status to draft.

## Verification

54 focused tests passed across location parsing, persistence, inbound workflow
and review. This includes normal subsequent intake, no legacy repeat question,
stale/source/identity/control/policy rejection, unchanged state after rejected
review, and preserving another human-review reason. Lint and mypy (99 source
files) passed. Full-suite results are recorded separately when complete.

Full-suite run completed with six failures (no exclusions). Three were legacy
receipt assertions requiring an unsupported residence claim; those were updated
to assert neutral acknowledgment AND prohibit the claim, preserving identity,
address, source, state and delivery assertions. The targeted combined rerun was
62 passed in 0.60s. Two failures correctly reject stale consultant/financial
provider evidence. One is a genuine unresolved sponsor journey regression:
single-dimension residence input currently enters human review too early and
interrupts the later address/replacement dialogue. Do not weaken that replay or
deploy this checkpoint; complete partial-location intake first. No claim of a
green full suite is made after these targeted fixes.

## Explicit remaining limits

This transaction has no new public endpoint or guided operator UI/CLI yet.
Its caller must own the Gmail state lock and operator authentication; an actor
string is not authentication. No real applicant was reviewed by these tests.
Original event linkage is checked, not the truth of purged email content.
Conflicting historical observations still need a source-bound resolution flow;
the transaction deliberately cannot choose a winner. Partial/uncertain location
intake and reviewer UX still need completion. Final customer-facing summary/PDF
presentation of these dimensions also needs implementation and visual inspection.

The actual Gmail worker was not restarted. Existing provider reports and CI
predate this code; they must not be presented as evidence for this checkpoint.
