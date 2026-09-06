# Gmail receipts for updates held during adviser review

Previously only updates to finalized cases queued a held-update receipt. A
same-applicant follow-up during HUMAN_REVIEW_REQUIRED was retained, but this
automatic sender did not acknowledge it. The existing receipt mechanism now
supports both hold reasons with distinct current-status checks and wording.

The review receipt says the follow-up was received and kept for review, that
review remains open, and that the customer need not resend the explanation. It
does not claim facts were merged, a document was checked, review was approved,
or a pack was generated. Finalized-update receipts retain their existing wording.

`queue_held_update_receipts` is wired into the Gmail sandbox send loop. The old
method name remains a compatibility entry point. Queueing binds the held event,
case/applicant/thread, preparation epoch and current processing-consent epoch.
Send-time checks revalidate source existence, unreviewed status, matching hold
reason/state, identity, thread, consent and latest outbox. Deterministic IDs and
INSERT OR IGNORE retain the existing duplicate suppression. Final attachments
remain excluded from automatic service replies.

## Captured evidence

- Extended English/Chinese, first/subsequent revision tests verify the new review
  hold alongside finalized holds, one send on repeat processing, exact saved
  body, reply threading, unchanged case state and still-held source updates.
- Mutation tests withhold queued receipts after status, owner, thread or pause
  epoch changes, or loss of the held source. The adapter is never called.
- A configured-consent test goes through a fictional applicant grant following a
  captured SENT notice; the held receipt records that actual epoch and sends.
  No test invents a real customer's consent.
- Initial sender/boundary/retry group: 40 passed (0.80s). The added consent-epoch
  test and boundary group: 6 passed (0.40s). Ruff and strict Mypy passed (93 modules).

No live Gmail traffic, service restart or deployment occurred. These results do
not prove recipient-side delivery, crash recovery against Gmail, operator UX or
finalpack release. Full end-to-end Gmail acceptance remains required.

Full development regression, collected before the final consent-epoch test:
**5,106 passed / 2 deselected**, 103.16 seconds, one existing Starlette warning.
The additional test passed separately in the six-test group above. The excluded
tests remain `test_current_journey_report_binds_all_source_and_probe` and
`test_current_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set`;
neither stale source-bound provider report was rewritten or counted as a pass.
