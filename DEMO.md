# Featured demo walkthrough

1. Run `make demo`.
2. Open `demo_output/demo_report.json` and confirm the first step lists `DATE_CONFLICT` and
   `MISSING_CERTIFIED_TRANSLATION` blockers.
3. Confirm the second step has no document blocker but still waits for explicit final confirmation.
4. Confirm the third step generates the ZIP with status `READY_FOR_HUMAN_REVIEW`.
5. Inspect `demo_output/outbound_inbox/` for the three threaded synthetic replies.
6. Run `make web` to inspect rules, issues, documents, and active evidence visually.
7. Run `make demo` again without `--reset` or replay through the integration test; persistent counts
   must remain unchanged.

The scenario deliberately demonstrates correction and abstention. It is not a happy-path-only chat
transcript.
