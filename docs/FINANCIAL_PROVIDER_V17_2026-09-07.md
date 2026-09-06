# Current-source financial PDF extraction

On `811d5e0`, four existing frozen fictional PDFs were rechecked and sent to the
real DeepSeek document extractor, once each. **4/4 passed**, **8,744 tokens**,
zero real documents and zero mailbox calls. Raw proposals, validated results,
actual response model, PDF hashes, all 93 source modules plus the probe, prompt,
schema and request-configuration fingerprints are retained in
`eval_output/financial_document_deepseek_2026-09-07-v17.json`.

No source PDF was recreated or modified. Their single-page previews were
regenerated from verified bytes and inspected using the PDF workflow. The clear
fictional markers, names, date, amounts and account endings were visible. This
does not establish performance on scans, arbitrary statements or complex layouts.

The observed records correctly distinguish gross annual GBP 42,000 salary,
GBP 12,500.50 and GBP 13,000 closing balances for account 1234, and the fictional
sponsor's HKD 88,000 balance for account 9876. All source dates are 2026-08-31.
The current-report test also explicitly verifies dates, salary basis/period and
account references after normal document validation, rather than trusting a
provider confidence score. This is extraction evidence, not a funding-sufficiency,
visa-outcome or official-policy determination.

The prior rollout and reports remain unchanged. A new separate current-run
receipt records the v17 hash. The current-source test enumerates the complete
source set rather than retaining the obsolete hard-coded 80-module count.
The financial and consultant report modules passed 55 tests (2.09s) before the
additional exact-date/account assertions; those are checked separately.

No production source, policy content, live service, customer approval or deployed
version changed in this checkpoint. Independent document challenges, full Gmail
delivery and nontechnical operator/release acceptance remain outstanding.

Full development regression completed with **5,124 passed, no deselections**,
103.57 seconds and one existing Starlette deprecation warning. Collection preceded
the final exact-date/account assertions; the financial module with those additions
passed separately (9 tests, 0.22s). Both formerly stale current-source report
checks now execute and pass. This removes those test exclusions, not the broader
delivery acceptance gaps described above.
