# Sponsor location conflict recovery

Previously, conflicting residence/presence observations could enter a review
hold but the operator transaction refused them without offering a source-based
resolution. The new optional `selected_source_event_ids` decision maps each
conflicting dimension to an existing registered source event. It supplies no
new fact value and does not delete or modify the original observations.

Selections require a current case fingerprint, current policy, actual processing
authorization when configured, the existing review hold, no pending held update,
and no uncertain send. Each selection must refer to the current sponsor identity
and epoch, and the selected event must have one unambiguous value for that
dimension. Unknown dimensions, missing sources, foreign-case sources, ambiguous
sources, unnecessary selection of nonconflicting dimensions and remaining
conflicts are rejected atomically.

The immutable review records selections and rationale. Normal intake, applicant
summary and answer JSON use selected sources while their source/identity binding
matches. A new observation invalidates this resolution; raw contradictions then
become visible again. Original source history remains private and intact.
Review clears only its own hold and invalidates applicant confirmations. It does
not send mail, grant consent, generate a pack or verify lawful immigration status.

Verification on 2026-09-07:

- Related workflow/review/summary/JSON regression: 116 passed in 1.66s.
- Final resolution and command tests, including added unknown-consent and
  unrelated-hold cases: 20 passed in 0.51s. This overlaps the preceding set.
- Full unfiltered suite: 5,391 passed, **2 failed**, one Starlette warning,
  114.92 seconds. The two failures are strict source-freshness checks for the
  previous consultant v11 and financial v19 provider reports: four implementation
  files changed. The reports and assertions were not overwritten or relaxed.
  Two subsequently added boundary tests passed in the focused run above; the
  full-suite count does not include them.
- Full lint and typing (102 source files) passed before those two test additions;
  the resolution test file also passed lint.

This verifies a local trusted-operator recovery transaction and serialization,
not an authenticated reviewer UI, a newly exercised live Gmail correction loop,
or a complete sponsor ZIP. Original-source truth still requires actual operator
inspection. Fresh provider runs and another full regression are required before
the current candidate can be described as all-green. The live Gmail worker and
its applicant's consent were not changed in this work.
