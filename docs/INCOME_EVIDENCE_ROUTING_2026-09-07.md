# Income-evidence routing repair

The actual v10 DeepSeek response classified “I don't get payslips. What should I
use to explain my income?” as unsupported. Existing deterministic guidance then
answered it, producing a contradictory generic lack-of-guidance opening.

The answer planner now removes that redundant boundary only for an entirely
covered income-evidence request, a known self-employed applicant, and guidance
within its reviewed date window. The original model response is unchanged.
An extra tax/other unsupported question, unknown or employed occupation, or
expired guidance retains the boundary. No new financial facts or assurances are
created. The actual document-checking caveat remains in the useful answer.

Verification: the first three real v10 proposals were replayed through the
fictional workflow with network denied. The complete final reply contains the
business-record/receipt guidance and source, lacks the contradictory opening,
and does not confirm or deliver anything. Targeted income/editorial regressions
passed 43 tests in 0.60s; existing customer-question and intent tests passed 156
in 0.38s. Lint and mypy (99 source files) passed.

No fresh model call, Gmail restart, external send or operator approval was made
in this checkpoint. The v10 evidence remains a truthful historical run and is
now older than this source change; its strict freshness check must not be
relaxed. Full regression, refreshed provider evidence and remaining end-to-end
delivery work are still required.
