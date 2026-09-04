# Gmail incremental intake: implementation status

Status: connected to the registered-sender automatic `serve` worker. The lifetime 100-message
limit is removed from that mode; manual `prepare` retains its original bounded trial behavior.

## Implemented and checked

`GmailAdapter.list_message_page` exposes a scoped page and its continuation without imposing
a total-message cap. `current_history_id` and `list_added_history_page` expose Gmail's history
cursor and newly added message IDs. These operations do not fetch message bodies or send mail.
History reads only `messagesAdded`, not the duplicated/general `messages` list or deletions.
History IDs retain their exact string representation and are never incremented arithmetically.

Fifteen contract tests cover pagination arguments, duplicate IDs, empty history, invalid cursors,
and separation of history-expired 404 from authorization failures, quota errors and server errors.
The complete local regression suite passes 256 tests; lint and strict typing pass.

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
power-loss recovery or a provider outage. The first crash-test run
failed because an acknowledgement assertion was accidentally attached to the wrong test;
the test layout was corrected before the complete suite passed.

The real authorized service mailbox accepted both scoped message-page and history reads.
The scoped query returned one message ID; history from the newly obtained cursor was empty
and returned a terminal cursor. No raw bodies were read and no email was sent.
See `eval_output/gmail_incremental_read_probe_2026-09-04.json`. This proves those read operations
work with the current authorization, **not** live multi-page recovery or correct durable intake.

## Runner integration and local rollout

The runner captures a baseline before scoped bootstrap, follows up to ten discovery pages per
cycle and commits each page through the journal. It completes history catch-up before processing.
History candidates are checked using metadata (sender, recipient, activation time, subject and
automatic/list/spam/trash/draft exclusions) before any raw-body read. Duplicate From headers fail
the boundary. Provider receipt timestamps determine oldest-first processing, not discovery order.
At most 100 bodies are processed per cycle, one at a time. Remaining candidates prevent dispatch.

Workflow/pack success precedes acknowledgement; workflow failures leave a candidate pending.
Rejected ingestion retains its failure record and journal reason. Unsent obsolete service drafts
are retained as FAILED with a specific withholding reason, without incrementing send attempts or
consuming the next dispatch slot. SENDING/attempted records and final-pack drafts are untouched.

The actual runner, with fake provider responses and offline extraction, processed 152 ordinary
messages over two cycles in chronological order, withheld dispatch after the first 100, and did
not re-fetch old bodies in an idle cycle. Separate tests cover expired history, scope rejection,
missing timestamps and a 152-draft backlog with an uncertain record preserved. This is local
integration evidence, not 152 emails sent to Gmail.

On 2026-09-04 the live worker was stopped, its SQLite state backed up privately, and restarted
with incremental intake. Initial scoped discovery found the existing enquiry, workflow deduplication
recognized it, and the journal recorded it as processed. Automatic dispatch was empty. The
original sandbox database SHA-256 remained
`537f74965b823fbba4c4b18b3cdc1b71b4cc0b7a5ca9aac283c2a0e7d583424d`.
No case, processed event, delivery history or original reply was reset.
After the final worker reload, an idle incremental cycle completed with zero withheld drafts
and zero dispatches, the same database hash, and one acknowledged journal candidate. There
was no repeated workflow processing of that old message in the idle cycle.

## Safety invariants implemented

1. Capture a baseline history cursor before bootstrapping the activation-scoped message list;
   retain it while following every full-sync page, then consume intervening history.
2. Each page's candidates and continuation persist together. Store only IDs
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
   history or pending candidates. Local tests cover overlap, crash before/after checkpoint commit,
   repeated page tokens, new mail during bootstrap and more than 100 messages.
7. Preserve existing live state during migration. Do not erase state or send duplicate messages
   to demonstrate the new mode.

## Remaining limitations

Live multi-page/expired-history recovery has not been exercised against Gmail. Deleted/unavailable
candidate metadata and invalid page tokens currently stop the cycle visibly rather than silently
skip candidates; an operator recovery procedure remains necessary. Metadata sorting reads all
pending candidates, so a very large pending backlog still needs additional resource bounding.
Equal receipt timestamps use message ID as a stable tie-breaker; that does not prove semantic
order for simultaneous conflicting replies. The service remains registered-sender only, local
to an awake/online Mac, and does not auto-send final packs.

## Provider contract

Checked 2026-09-04 against Google's official [synchronization guide](https://developers.google.com/workspace/gmail/api/guides/sync),
[history listing reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list)
and [message listing reference](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list).
History may expire; a history 404 requires full synchronization. Pagination must finish before
using the returned history cursor for the next incremental request.
