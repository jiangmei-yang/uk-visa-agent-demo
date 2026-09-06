# Fresh financial-document extraction v18

The four retained fictional PDFs were processed with four new DeepSeek calls
against commit `cb19a5d263cafe2613a2906b2ea15bf50e0cb24f`, after the delivery
formatter changes. All four passed, using 8,744 reported tokens. Requested and
reported model: `deepseek-v4-flash`. No Gmail calls or real documents were used.

The probe checked every selected file against the frozen source before any
external call and refreshed its preview from those bytes. All four previews were
viewed; no source PDF was regenerated or changed. The complete raw proposals and
validated observations were inspected, including:

- Kai Example: gross annual salary GBP 42,000, letter date 2026-08-31, no account
  reference asserted for the employment letter;
- Kai Example: closing GBP 12,500.50 and corrected GBP 13,000, both dated
  2026-08-31 and account ending 1234;
- Mina Example, not the applicant: closing HKD 88,000, dated 2026-08-31, account
  ending 9876.

Subject, amount, date and account observations retain source excerpts and page
references. The current replay checks also require dates, salary period/basis and
account references; the probe's shorter visible check list alone is not treated
as verification of those extra fields.

`financial_document_deepseek_2026-09-07-v18.json` and its separate v18 receipt are
new evidence. Earlier provider reports, the v17 receipt and the historical rollout
remain unchanged. The current test reference is updated without weakening the
complete implementation, prompt, schema, configuration or PDF source checks.
Financial plus consultant replay modules passed **55 tests in 2.01 seconds**;
full lint and configured typing also passed.

This refresh closes the known financial source-freshness mismatch for the current
runtime. It does not establish complex transaction analysis, genuine document
authenticity, funds sufficiency, sponsor support terms, independent accuracy or
recipient-side delivery. There were no new Gmail sends or worker reloads.

After updating both current provider references, the complete no-exclusion suite
passed **5,153 tests**, with one existing Starlette warning, in **103.51 seconds**.
No runtime source was edited during that run. This restores local full-suite
validation; it is not itself a new GitHub CI or deployed-service acceptance result.
