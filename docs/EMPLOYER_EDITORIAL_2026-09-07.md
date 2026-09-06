# Employer conversation receipts

The preceding actual DeepSeek experiment exposed missing concrete receipts and
repeated introductions despite passing its state checks. This change addresses
those observed reply defects without making new extraction or release decisions.

- New employer name, address and phone values are now explicitly acknowledged in
  English/Chinese, retaining the exact supplied value. They are not described as
  independently verified, and receiving a phone no longer produces only a generic
  “existing details will stay on file” message.
- Employer-only corrections use a sentence instead of an internal field label.
  Mixed corrections retain the existing complete receipt. The existing warning
  about a prior employment letter needing review still runs after the receipt.
- A deferred employer detail opens with its concrete deferral receipt instead of
  restarting a greeting/reassurance paragraph. Other deferral and safety behavior
  is unchanged.
- The home-address question already explains why it is needed and distinguishes
  it from a workplace address; its duplicate pre-question rationale is omitted.

## Evidence and limits

`eval_output/employer_intake_2026-09-07-v2-replay-editorial.json` passes 9/9 saved
real-model turns through the changed workflow. It is **offline replay**, not a
fresh provider experiment and not live Gmail. The intermediate `replay-receipts`
report is retained, not presented as the final editorial state.

Focused receipts/workflow/replay checks passed 22 tests; the replay test then
gained explicit assertions for the observed wording defects and passed separately.
The initial six new unit tests failed because their synthetic Case omitted the
required policy_version; the fixture was corrected without relaxing validation.
Ruff and strict Mypy passed for 93 modules.

Manual inspection of the nine replayed replies confirms concrete contact values,
no repeated welcome on address/uncertainty turns, a readable correction receipt,
and a single home-address explanation. It still shows repeated “Thanks, I've noted
that” wording and an awkward generic transition after proactive financial guidance.
These remain editorial debt: no universal naturalness or accuracy score is claimed.
Broader unscripted multilingual testing and current-code Gmail, full-source
provider reports, deployment and finalpack acceptance remain outstanding.

Full development regression: **5,066 passed / 2 deselected**, 103.67 seconds,
one existing Starlette warning. The two stale source-bound provider tests remain
excluded: `test_current_journey_report_binds_all_source_and_probe` and
`test_current_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set`.
These are outstanding release evidence, not waived acceptance requirements.
