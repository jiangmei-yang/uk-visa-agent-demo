# Residence question transport ordering

## Defect and boundary

The current-home duration short-answer path previously checked the saved question,
current address, recipient, thread and SENT outbox payload, but did not compare
the send timestamp with the inbound email timestamp. A delayed inbound message
could therefore be associated with a question sent after that message arrived.

The contextual path now additionally requires a parseable, timezone-aware
`sent_at` strictly earlier than the inbound `received_at`. Missing, malformed,
timezone-less, future and equal timestamps cannot authorize that inference.
Offsets are compared as instants, not lexically. This affects both short supplied
durations and short uncertainty replies. A full, independently grounded statement
of current-home duration does not depend on this contextual authorization.

This is a fail-closed attribution check, not proof of human reading or a provider
delivery receipt. An equal timestamp is ambiguous and does not authorize a
short-answer inference. It does not migrate old cases, rewrite historical send
times, send mail or restart the Gmail worker.

## Evidence

- Workflow/reopened real-proposal replay group: **20 passed** (0.94s), including
  negative cases for both a supplied duration and “I need to check.”
- Positive timezone-offset case preserves the legitimate short answer.
- Existing independent full-statement, correction, new-home invalidation and
  uncertainty persistence tests remain passing.
- Saved real v2 residence proposals replayed offline: **4/4**, report
  `eval_output/residence_duration_2026-09-07-v2-replay-sent-order.json`.
- Ruff and strict Mypy passed (92 source modules).
- Development-wide regression: **4,967 passed / 2 deselected**, 102.10s, one
  existing Starlette test-client deprecation warning. The exclusions are the
  historical source-bound consultant and financial provider reports; neither is
  relabelled current or counted as release acceptance.

No new model calls or live Gmail traffic occurred. These are attribution and
regression checks, not an overall accuracy/naturalness score or release approval.
