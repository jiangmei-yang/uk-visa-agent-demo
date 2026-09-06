# Journey report evidence binding, version 2

The journey probe previously hashed its Python source and bundled assets but
omitted the policy YAML actually loaded. That was insufficient for claiming
rule-bound acceptance. New reports now include that exact file, `pyproject.toml`
and `uv.lock` in `source_sha256`; missing required files fail before model calls.
No old report is modified to retroactively claim these bindings.

Reports also retain the policy identity/version/date interval, the fixed test
clock, captured transport, disabled model rendering, extraction-attempt limit,
and a bounded set of installed dependency versions. Installed versions supplement
the dependency lock hash; they do not establish a clean locked installation.
`current_on_evaluation_date` means only that the test clock falls inside the
policy's declared interval. It is **not** a fresh official-source verification.

The source allowlist does not include environment dumps, `.env`, `.secrets` or
mailbox state. Mutation tests show that changing either policy or dependency
declarations changes the manifest; a missing policy is not silently omitted.

`eval_output/employer_intake_2026-09-07-v2-replay-policy-bound.json` is a new,
explicitly offline replay with 9/9 contract checks, zero model/mailbox calls,
and the new bindings. It does not replace a source-current live-provider run.
Focused manifest/employer/sponsor replay tests: 8 passed; Ruff passed. The initial
Ruff import-order finding was corrected and rechecked.

Expanded related journey regression: **53 passed / 1 deselected**, 2.33 seconds.
The excluded test is `test_current_journey_report_binds_all_source_and_probe`,
whose old live report remains stale. No full-suite rerun is claimed for this
probe-only change; production source is unchanged from the preceding checkpoint.

No production workflow behavior, policy content, real customer data, runtime
service, Gmail delivery, operator decision or deployment was changed. Outstanding
full-source live-provider and release checks remain outstanding, not waived.
