# Source-bounded cover-letter wording

The draft previously printed internal occupation/funding categories as applicant
prose, for example `My recorded occupation status is student`. The production
letter now uses a bounded context renderer with ordinary sentences for student,
employment and self-employment, and self-funding or an identified personal sponsor.
Employer/sponsor names retain their literal spelling. This is deterministic
editing of recorded facts, not an additional model call or a new source of facts.

The employer-or-school category does **not** identify which organization pays.
Neither occupation nor the employer name resolves that ambiguity. Instead of
inventing a payer or financial promise, the draft explicitly requests adviser
confirmation of the organization and support terms. Missing/unsupported values
remain review notes rather than `None` or legacy enum strings in applicant prose.
No return-to-work promise, family relationship or commitment to all expenses is
added. Profiles, sources and delivery gates are unchanged.

## Evidence and limits

- Twelve new checks cover all three occupation states, literal employer names,
  independent payer identity, organization ambiguity across occupations, missing
  information and unsupported values. They pass after implementation; their
  initial run failed collection because the new renderer did not yet exist.
- Actual ZIP integration now extracts the generated letter and requires the
  student sentence and unresolved organization note, rejecting the old internal
  phrasing. Combined letter, pagination, Unicode and demo-pack tests: **28 passed
  in 0.96 seconds**. Full lint and configured typing passed.
- `scripts/cover_letter_context_qa.py` generated a separate fictional comparison
  PDF. `pdfinfo` confirmed one page, and that whole page was visually inspected:
  all five contexts were readable with no clipped lines or overlap.

This is a wording repair for one paragraph, not a polished final submission
letter. The letter still uses the generic table-style pack renderer. Specific
organization identity/support-term intake remains incomplete; an explicit review
note is not equivalent to collecting that information. Independent applicant
review, broader letter design and real Gmail delivery remain outstanding.
Old provider reports and CI do not cover this changed runtime; their freshness
gates must be satisfied before a new release claim. No Gmail reload or send was
performed for this change.
