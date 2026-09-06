# Distinct sponsor observations enter the persisted workflow

The consented, owner/order-checked business path now records bounded explicit
sponsor-location statements after ordinary validated profile updates. A model
omission or off-topic proposal cannot bypass this capture. Identity is bound to
the resulting current sponsor; unknown/non-personal funding does not use the
helper. Existing pre-processing guards still reject other senders, older events
and finalized-case updates before extraction/capture.

New observations are retained with their source IDs and excerpts. They clear the
legacy location boolean, supersede its active evidence without deleting history,
invalidate profile/final confirmations and participate in summary fingerprints.
The current implementation enters human review with a specific reason and reply,
so an old false value cannot continue to waive location-related evidence. It does
not infer a legal-status fact from residence or presence.

## Tests

- Four English/Chinese mixed-location cases, with ordinary or deliberately
  off-topic model output, traversed the actual workflow and SQLite store.
  Both dimensions persist, the old boolean/confirmations lose authority, the
  summary fingerprint changes and no ZIP is produced. Duplicate delivery keeps
  exactly two observations and database reopen preserves them.
- Wrong sender, old event and finalized case: no new observations, unchanged old
  boolean and zero extractor calls. The focused module passed **7 tests in 0.32
  seconds** after adding these controls and specific review-reply assertions.
- Earlier parser/storage/automatic-reply/progression group: **76 passed in 1.53
  seconds**, before the final reply/boundary test additions. Lint and configured
  typing passed for 97 source modules.

## Explicitly incomplete development state

All newly parsed location statements currently enter this review state, including
single-dimension statements. This is temporary containment, not the final customer
experience or completed material-applicability logic. The typed observation review
decision and resumption flow do not yet exist; blindly retrying the same text with
a new event ID would not constitute approval and may hold again. Do not deploy this
intermediate behavior as a finished feature or imply an adviser has reviewed it.

Next: source/identity-bound review or clarification of dimension completeness,
explicit UK-status evidence applicability, summaries that show both dimensions,
and fresh customer confirmations after a real completed review. Sponsor replacement
history needs dedicated tests. Generic consent/order guards are reused, but these
fixture tests are not a new real-applicant consent or Gmail acceptance trial.
Full-suite/provider/CI freshness remains to be restored after this feature is
integrated; historical reports and the running service are unchanged.
