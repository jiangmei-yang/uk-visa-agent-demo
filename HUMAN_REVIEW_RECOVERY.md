# Human-review recovery: partial implementation

The previous rejection path marked a paused/finalized/out-of-order applicant message processed
but retained only a reason. In the queue worker, completion also cleared the original queue body.
The local case record therefore could not reconstruct that customer's unprocessed update.

`held_inbound_events` now records the complete parsed event (including body, timestamp, channel,
thread and attachment references) atomically with the hold reason and processed-event marker.
Only same-applicant review holds are retained; sender-mismatch messages do not enter this store.
The case snapshot, confirmation state and delivery gate remain unchanged. No reply or delivery is
created by recording a hold. Replay remains idempotent.

The private case export includes these records. Case deletion removes the held records from
SQLite. Attachment references are not new copies of file bytes: original upload retention,
filesystem backups and the provider mailbox are separate, and are not silently erased here.
No scheduled retention expiry or claim of encrypted storage has been added.

Six local integration cases cover paused/finalized cases through direct and queued processing,
restart/replay, queue-payload cleanup, case export/deletion, sender mismatch and injected storage
failure. Failed payload persistence cannot mark an event processed. This is preservation evidence,
not an end-to-end human-review resolution test.

## Still required

- A reviewed operator action to inspect these held updates and resolve the original reason.
- An actor, reason and before/after audit trail for any permitted resumption or revision.
- Controlled reprocessing without deleting the original event history or implicitly accepting
  unsupported routes, invalid documents or facts lacking sources.
- A fresh, context-bound applicant confirmation whenever reviewed facts/documents change.

There is deliberately no automatic “clear review” operation. Preserving the update fixes data
loss, but does not yet make the manual-review workflow complete. Operators must not directly
edit case status or erase processed-event IDs to simulate a successful recovery.
