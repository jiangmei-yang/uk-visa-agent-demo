# Sponsor facts in customer confirmation emails

Profile and final email confirmation summaries now show residence and current
physical presence as separate customer-reported facts. They suppress the
ambiguous legacy boolean whenever typed location history exists. The summary
explicitly distinguishes reported location from verified lawful UK status.

Only the current sponsor identity and epoch contribute to the displayed values.
Missing dimensions remain unconfirmed; contradictory statements are shown as
needing clarification. Changing sponsor identity or epoch cannot display an old
sponsor's answers as the current person's facts. Self-funded cases do not receive
these location rows. Internal event IDs and reviewer metadata are not disclosed.

Verification: 39 tests passed in 0.55s across the new summary, source-bound review
and operator command. Both languages and both confirmation types are covered,
including missing/conflicting/stale data, rendering without state mutation and
changed dimensions invalidating both confirmation fingerprints. Lint and mypy
(101 source files) passed.

This change is email summary presentation, not PDF/ZIP completion. Those final
artifacts still require separate implementation and visual inspection. The real
Gmail worker was not restarted and no actual customer confirmation was created.
Fresh provider evidence, full regression and end-to-end acceptance remain open.
