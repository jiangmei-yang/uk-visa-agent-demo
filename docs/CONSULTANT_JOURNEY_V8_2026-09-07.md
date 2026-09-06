# Current-source real DeepSeek conversation experiment

On source `c147f12`, the existing four exposed development journeys were run
again with the actual `deepseek-v4-flash` extraction adapter. The model was not
stubbed and the replies were not old proposals replayed. Each turn used reopened
SQLite state, the outer guard, reviewed reply generation and captured Gmail
dispatch. **22/22 turns passed**, 22 model calls, **124,392 tokens**, zero retries,
zero mailbox calls and no real documents. The raw attempt is retained as
`eval_output/consultant_journey_2026-09-07-v8.json`.

Coverage: Chinese student preparation obstacles and funding changes, English
self-employment/host-versus-payer reasoning, and durable bilingual response-length
preferences with one-turn overrides. The fixed scenario clock is 2026-09-06;
run timestamps separately record the actual execution date. This is not a new
independent holdout, an arbitrary-document evaluation or a live Gmail trial.

The report binds the complete current source/assets/probe manifest, policy YAML,
dependency declarations, installed version metadata and effective workflow
configuration. The current-report test now verifies this actual run, rather than
excluding the stale v7 result. Old reports remain unchanged. Report verification
checks actual usage/raw outputs, completed results and non-replay provenance;
passing saved-proposal replay alone cannot satisfy it.

## Manual reply review

All 22 displayed replies were read. The original repeated-date/known-birthday
failures did not recur in these cases. Replies supply actionable preparation
steps, an official application entry when requested, refuse approval guarantees,
and distinguish own-account transfers from earnings. They preserve paused
preparation and revert from a one-turn detailed answer to the saved brief style.

Remaining editorial observations include a generic “personal sponsor” correction
opening before the more specific mother-funding explanation, an awkward comma
in the English name/birthday receipt, and template-like repeated funding advice.
These are not hidden by the contract pass. Keyword, length and state checks do
not constitute a human naturalness score or universal factual accuracy.

The complete related journey test module passed **46 tests** (1.95s), now including
the current-source report binding. The financial document provider report is
still stale and remains a separate release requirement. No production restart,
real message, operator approval, GitHub push or final delivery occurred.

Full development regression: **5,123 passed / 1 deselected**, 102.72 seconds,
one existing Starlette warning. The sole excluded test is now
`test_current_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set`.
The consultant current-source test is no longer excluded. Future production
source changes must invalidate the binding until another actual run verifies them.
