# Featured demo walkthrough

For a nontechnical interviewer, use [START_HERE.md](START_HERE.md) and the
**Try the offline workflow** page. It needs no model key or mailbox login.
This page describes the separate developer replay and the Gmail acceptance path;
neither an offline replay nor a running Gmail worker proves real final delivery.

## Developer replay — dedicated synthetic state only

`make demo` always supplies `--reset`. Never run it against a customer database,
or use it to claim duplicate-message protection. Use an explicitly isolated
database and output directory for the first replay and then rerun the same
non-reset command:

```sh
VISA_AGENT_DATABASE=data/interview-replay/visa_agent.db \
VISA_AGENT_OUTPUT_DIR=demo_output/interview-replay \
uv run visa-agent demo
```

The paths below are relative to that dedicated output directory.

1. Run the command above.
2. Open `demo_report.json` and confirm the first step lists `DATE_CONFLICT` and
   `MISSING_CERTIFIED_TRANSLATION` blockers.
3. Confirm the second step has no document blocker but still waits for explicit final confirmation.
4. Confirm the third step generates the ZIP with status `READY_FOR_HUMAN_REVIEW`.
5. Inspect `outbound_inbox/` for the three threaded synthetic replies.
6. For a console on this same state, use the same two environment variables with
   `uv run visa-agent web --port 18080`; choose another available port if needed.
   `make web` alone would select its default database instead.
7. Run the same non-reset replay command again. Persistent counts must remain
   one case, three processed events, three outbox rows and one delivery. The
   integration test `test_demo_generates_source_linked_pack_and_is_idempotent`
   independently checks this behavior in temporary state.

The scenario deliberately demonstrates correction and abstention. It is not a happy-path-only chat
transcript.

## Gmail demonstration — real transport, supervised release

Use [the registered service instructions](GMAIL_AUTOMATIC_SERVICE.md). Do not
change an existing state directory's sender binding or reset it to onboard a new
participant. The interviewer need not use a special subject or the word “test”.

1. The registered participant sends an ordinary, attachment-free enquiry from
   their own mailbox. Check that the service's processing notice actually arrives.
2. The participant reads it and, if they agree, replies in the same thread with
   its current reference. Verify the real grant; do not manufacture one in the
   database or treat an operator's API permission as applicant consent.
3. Continue naturally: leave dates undecided, ask for the application link,
   provide facts in a different order, and correct a previously supplied fact.
   Check both the received reply and case memory. No exact test sentence is
   required, and repeating already answered questions is a failure to record.
4. Use clearly fictional, non-personal documents after consent. Check missing
   evidence, contradictions and corrections without replacing original sources.
   Any required operator review must be explicit and recorded; a receipt alone
   is not evidence that a correction was accepted.
5. Confirm the latest summaries as the applicant. Withhold the final package
   when review, evidence or current confirmation is missing. The automatic worker
   intentionally does not dispatch final `ready` replies.
6. After explicit operator inspection and reviewed dispatch, open the email in
   the recipient's mailbox, download the ZIP and inspect its contents against the
   confirmed case. Provider acceptance or an outbox `SENT` row is insufficient
   recipient-side evidence. Never resend an uncertain attempt before reconciling it.

Record each checkpoint as observed, failed or not yet attempted. These are
acceptance criteria, not a claim that the current candidate has passed all six.
See [remaining acceptance](docs/END_TO_END_ACCEPTANCE_AUDIT.md) for current gaps.
