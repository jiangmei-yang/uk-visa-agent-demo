# Preserve literal applicant values in generated summaries

The profile formatter previously replaced underscores and capitalized every
string containing an underscore. Seven fictional name/address/company examples
failed before the fix: their stored JSON remained correct but the displayed PDF
value differed. This is a material source-fidelity defect, not cosmetic taste.

Formatting is now restricted to the internal visit-purpose, occupation, funding
and sponsor-relationship labels. Other strings retain their original case and
separators. The profile and its source evidence are never changed by the renderer.

Verification: all seven formerly failing examples now pass, alongside the enum
readability control and related employer/residence/demo-pack tests: **22 passed**,
0.84s. Ruff and strict Mypy passed (93 modules). The reproducible fictional script
`scripts/profile_literal_output_qa.py` uses the production PDF formatter and
reopens the PDF to assert all seven exact strings. The one rendered page was also
visually inspected with no overlap or clipped text.

This is a targeted repair, not a full source-to-document accuracy audit. It does
not assert that all input types, international names or existing historical PDFs
are correct, and it does not modify old evidence. No live model call, Gmail send,
full-suite rerun, deployment, operator approval or final release occurred.
