# Employer applicability in the rendered summary

The b2f1cfc container check showed three empty employer fields labelled “Not
provided” for the student demo. That misleading display is now corrected without
changing the underlying profile, requirement rules or release authority.

- For recorded student/self-employed status, absent employer fields say “Not
  applicable to the recorded occupation”. This describes the current bounded
  single-occupation profile, not every possible simultaneous work/study situation.
- Unknown occupation says “Applicability not yet established”.
- Employed status with no supplied detail remains “Not provided”.
- Active deferred fields with a matching current-employer deferral record say
  “Deferred for checking - not yet supplied”. Old-employer deferrals do not apply.
- Actual supplied values are preserved even in inconsistent legacy snapshots,
  rather than hidden under an inapplicable label.

The profile JSON remains unchanged; these strings are display state, not new
applicant facts. No review or confirmation is granted by formatting.

Verification: 29 targeted demo-pack, employer workflow and output tests passed
(1.44s), plus Ruff and strict Mypy (93 modules). `scripts/employer_output_qa.py`
generated a labelled fictional one-page PDF with the production formatter;
the rendered page was inspected with no clipped or overlapping text. The local
font configuration was supplied explicitly, avoiding the prior renderer warning.

This is not a new full-container run, live Gmail test or full-suite result.
The previously recorded container/archive remains immutable evidence of its old
source. Full summary/PDF/JSON applicability QA, multiple occupations, sponsor
unknown applicability and polished cover-letter wording remain separate work.
