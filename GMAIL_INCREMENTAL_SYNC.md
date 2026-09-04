# Gmail incremental intake: implementation status

Status: API boundary and durable journal verified; **not yet connected to the live worker**. The live runner
still has the explicit 100-message full-batch limit described in `GMAIL_AUTOMATIC_SERVICE.md`.

## Implemented and checked

`GmailAdapter.list_message_page` exposes a scoped page and its continuation without imposing
a total-message cap. `current_history_id` and `list_added_history_page` expose Gmail's history
cursor and newly added message IDs. These operations do not fetch message bodies or send mail.
History reads only `messagesAdded`, not the duplicated/general `messages` list or deletions.
History IDs retain their exact string representation and are never incremented arithmetically.

Fifteen contract tests cover pagination arguments, duplicate IDs, empty history, invalid cursors,
and separation of history-expired 404 from authorization failures, quota errors and server errors.
The complete local regression suite passes 249 tests; lint and strict typing pass.

`GmailSyncJournal` now binds a private SQLite journal to one intake scope and commits each
page's candidate IDs and checkpoint in one transaction. Full bootstrap retains its baseline
history cursor and requires a history catch-up before becoming ready. Partial history pages
retain their original start cursor. Replayed/stale responses and cyclic tokens are rejected;
expired-history resync retains pending and previously acknowledged candidates.

Ten local integration tests cover 251 full-sync candidates plus one arriving during bootstrap,
reopening state, deduplication, partial-history cursor handling, injected SQL write failure,
scope mismatch, acknowledgement outcomes and two actual child-process exit windows. Exiting
with code 75 before commit leaves neither candidate nor advanced checkpoint; exiting after
commit preserves both. This verifies SQLite process-crash atomicity in these windows, not
power-loss recovery or the still-unintegrated live Gmail worker. The first crash-test run
failed because an acknowledgement assertion was accidentally attached to the wrong test;
the test layout was corrected before the complete suite passed.

The real authorized service mailbox accepted both scoped message-page and history reads.
The scoped query returned one message ID; history from the newly obtained cursor was empty
and returned a terminal cursor. No raw bodies were read and no email was sent.
See `eval_output/gmail_incremental_read_probe_2026-09-04.json`. This proves those read operations
work with the current authorization, **not** live multi-page recovery or correct durable intake.

## Required before rollout

1. Capture a baseline history cursor before bootstrapping the activation-scoped message list;
   retain it while following every full-sync page, then consume intervening history.
2. Wire the tested journal into the runner so each page's candidates and continuation persist together. Store only IDs
   until sender/recipient/activation/loop boundaries have been checked. History has no sender
   query: history candidates must not automatically become applicant messages.
3. Keep the original history start cursor throughout pagination. Advance to the returned
   terminal cursor only after candidates are durably recorded, with replay-safe deduplication.
4. Process eligible messages in chronological order. Mark a candidate complete only after its
   workflow commit and required pack materialization. Failed processing must remain retryable;
   permanent ingestion failures must remain visible, not vanish with the cursor.
5. Do not send a reply while older intake pages/candidates remain undispatched to the workflow.
   Reconcile prior uncertain sends independently, as the current worker already does.
6. On expired history, rebuild the scoped discovery set without clearing cases, events, outbox
   history or pending candidates. Test overlap, crash before/after checkpoint commit, repeated
   page tokens, invalid tokens, new mail during bootstrap and more than 100 messages.
7. Verify migration from existing live state and a no-change cycle before enabling automatic
   processing. Do not erase state or send duplicate messages to demonstrate the new mode.

## Provider contract

Checked 2026-09-04 against Google's official [synchronization guide](https://developers.google.com/workspace/gmail/api/guides/sync),
[history listing reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list)
and [message listing reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list).
History may expire; a history 404 requires full synchronization. Pagination must finish before
using the returned history cursor for the next incremental request.
