# PDF sponsor-location presentation checkpoint

The production pack's profile rows now use the same current identity/epoch-bound
location facts as customer confirmation emails, in English for the pack. When
typed observations exist, the ambiguous legacy location row is suppressed.
Missing/conflicting/current facts stay distinct; location is not described as
verified lawful status. Rendering does not mutate the customer case.

`scripts/sponsor_location_summary_qa.py` generated the fictional single-page
`output/pdf/sponsor-location-dimensions/summary.pdf` through the production PDF
formatter. All pages (one) were rendered and visually inspected: no clipping,
overlap, lost rows or footer-only extra page. The QA illustrates different
dimensions, missing presence, conflicting residence and replaced sponsor. It is
not a released customer pack or an approval of those incomplete cases.

17 summary/profile-row tests passed in 0.16s; lint and mypy (101 source files)
passed. Existing literal-profile and sponsor-applicability regressions were also
run separately. Original QA artifacts were preserved, not overwritten.

Still pending: structured application-answer JSON for the separate dimensions,
a full gated sponsor pack and its visual inspection, current provider/full-suite
evidence and real Gmail final recipient validation. No Gmail deployment or
customer-state change was performed. This is not a full delivery claim.
