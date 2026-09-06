# Sponsor applicability in the customer summary

The previous formatter replaced every sponsor field with `Not applicable` whenever
funding was not `personal_sponsor`. This incorrectly treated unknown funding as
confirmed inapplicability and hid supplied legacy facts, including a recorded
negative answer about the sponsor's UK location.

The formatter now distinguishes:

- unknown or unrecognized funding: missing sponsor fields show
  `Applicability not yet established`;
- explicitly self/employer-or-school funded: missing fields show
  `Not applicable to the recorded funding source`;
- personal sponsor with absent details: `Not provided`;
- supplied values: preserve them for review regardless of the funding label.

This changes display only, not stored facts, evidence, policy requirements or
confirmation authority. The normal funding-change workflow still supersedes and
clears old sponsor evidence. Rendering an inconsistent legacy snapshot must not
silently perform that business mutation or hide the inconsistency.

## Evidence

Thirteen new parameterized checks initially produced ten failures against the old
formatter. After the change they passed, including true/false preservation and
before/after case snapshot equality. The related employer/literal display group
passed 28 checks. The sponsor-address group plus new tests passed 34 checks after
replacing its old assertion that explicitly required hiding a supplied address.
Full lint and configured typing passed.

`scripts/sponsor_output_qa.py` generated a separate fictional summary using the
production formatter. The initial inspection covered only the first page: the
five states, literal underscore address and negative UK-location answer were
readable. A subsequent complete page inventory found a second, footer-only page;
the earlier claim that this was a one-page artifact was incorrect. Both the
original PDF and this correction are retained. The trailing-spacer defect is
documented in `PDF_PAGINATION_REPAIR_2026-09-07.md`. This is a QA artifact, not a
customer delivery.

The first full-suite run captured the older sponsor-address test before it was
updated and failed that assertion plus two model-report freshness checks. A fresh
full-suite rerun after the test update completed with **5,135 passed, 2 failed,
one existing Starlette warning in 104.72 seconds**, with no exclusions. Both
remaining failures are the source-binding checks below. The two report checks were
also rerun separately and both correctly reject the changed `delivery/pack.py`
hash. Existing consultant v8 and financial v17 reports remain unchanged; they are
not fresh model evidence for this modified runtime. Do not deselect those checks
or relabel their reports as current to claim a green release.

The Gmail worker remains on the previously CI-verified imported runtime. This
formatter change has not been deployed to it or accepted as a completed release.
