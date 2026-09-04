# Human-review recovery: controlled Gmail retry and versioned revision

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

## Local operator workflow

Use the installed project's Python environment, an existing registered Gmail state directory and
the exact case ID from the private console. Inspection prints private message content; do not
paste it into public issues or commit the output.

```bash
uv run python scripts/review_held.py inspect --state-dir data/registered-applicant --case CASE_ID
uv run python scripts/review_held.py retry --state-dir data/registered-applicant --case CASE_ID \
  --event HELD_EVENT_ID --fingerprint FINGERPRINT_FROM_INSPECTION \
  --actor "Reviewer name" --reason "What was reviewed and why normal extraction should be retried"
```

Do not execute the second command until the original review reason and selected update have
actually been examined. This is **permission to retry normal validation**, not a conclusion that
the documents are valid or the application is suitable. The operator identity is locally asserted;
this CLI relies on filesystem access and is not authenticated reviewer-role management.

The action acquires the same state lock as the Gmail worker. It rejects changed case snapshots,
non-Gmail cases, finalized/delivered cases, older held updates, missing reasons, repeated retries
of the same update and unresolved SENDING records. One transaction records actor/reason and
before/after snapshots, invalidates all prior confirmation fields, and queues a new linked retry
event. Original held and processed-event records remain intact. Facts, documents and risk flags
are not edited. No email or pack is sent by the CLI.

The next `serve` cycle consumes the private `gmail_review` queue through the existing workflow
and natural document reader before new intake. Pending/retrying/failed review work prevents new
intake and dispatch until resolved. Successful processing can still return to human review if
the underlying uncertainty remains. Subsequent summaries require fresh customer confirmation.

Nine local retry integration cases cover old-consent invalidation, retained risk facts, normal
reprocessing, restart, original-event preservation, audit/export/deletion, invalid operator inputs,
finalized-case refusal, uncertain-send refusal and transaction rollback on queue-storage failure.
No real applicant review action has been executed as part of these tests.

## Final-delivery hold after new information

A local regression reproduced a gap: an ordinary correction after pack generation was held,
but the old ready outbox row could still be sent. Both Gmail and WhatsApp sender contracts
returned SENT in the initial failing test (fake providers; no real outdated pack was sent).
Final delivery now checks for unreviewed held applicant updates before provider invocation.
The local pack-download endpoint also returns HTTP 409, retaining the historical ZIP on disk.
Completed linked intake retries no longer count as unreviewed holds; queued/failed retries do.

Additional local checks verify that an unrelated sender cannot block delivery and that a send
already accepted by the provider still reconciles to SENT without retry or deleting the hold.
This cannot recall an already sent attachment, and does not prove atomic cancellation of a
provider request if new mail arrives during that request. The serialized Gmail worker and the
pre-send check protect updates already known locally; late corrections still need reviewed revision.

## Remaining human-review work

Authenticated operator roles and a non-technical review UI remain unfinished. The retry action does
not approve documents, resolve unsupported routes or retry older out-of-order updates. Finalized
packs use the separate, explicitly authorized revision action below. Failed queued retries require investigation; do not erase
their records or manipulate processed-event IDs to manufacture a successful recovery. A complete
real-user human-review/resumption journey is still unverified.

## Gmail acknowledgement of post-pack updates

The registered-sender service now queues a durable `held_update_received` receipt for retained
`FINALIZED_CASE_NEW_EVENT` updates in Gmail cases. It runs after intake discovery drains, respects
the existing allowed recipient, skips older updates and already actioned review events, and leaves
the case snapshot and original held record unchanged. Repeated cycles do not create another receipt.
It says the update was recorded, prior-pack downloading/further sending is paused, and no revised
pack has been prepared or sent. It does not claim an assigned reviewer, automatic revision or recall
of an attachment already delivered. The provider-bound wording is regenerated from the held record
and current case state, then persisted before sending; changed review state withholds a stale receipt.

Five local capture tests cover both languages, generated/delivered statuses, recipient scope,
threading, replay and changed-state refusal. All 371 tests pass. There is no actual post-delivery
applicant exchange in that evidence. The receipt alone does not resolve the revision; the local
revision workflow added below is separate evidence and does not claim a real recipient exchange.

## Versioned post-delivery revision — local implementation, 2026-09-04

Inspect the current private case and every retained update before authorizing a revision:

```bash
uv run python scripts/review_held.py revise --state-dir data/registered-applicant --case CASE_ID \
  --event EARLIEST_HELD_EVENT_ID --fingerprint FINGERPRINT_FROM_INSPECTION \
  --actor "Reviewer name" --reason "What changed and why the retained updates should be revalidated"
```

If more than one update is retained, the command refuses without `--include-held-updates`. That
flag explicitly authorizes the complete currently held batch, selecting its earliest event. Each
member must belong to the same applicant/thread, be a finalized-case update, and not predate the
last processed applicant turn. A bad member rejects the entire transaction. The first action is
audited as `revision`, subsequent members as `revision_update`, with actor/reason, before/after
snapshots and independently linked retry IDs. This is not permission to invent or approve facts.

The action verifies the original ZIP's registry/path/hash, refuses uncertain SENDING or AMBIGUOUS
sends, archives its metadata, increments `Case.delivery_revision`, clears all case-level summary
confirmations and the current pack path, and withholds prior PENDING/RETRY replies. SENT messages
and original bytes remain untouched. It queues normal validation; no external mail or pack is
created by the command. The same worker lock guards this change; there is no authenticated RBAC.

`gmail_review` consumes the earliest unresolved queued item only. A failed item or a not-yet-due
retry prevents later updates from overtaking it. Pack generation, ready planning, download and
delivery stay withheld while retained updates remain unprocessed. After fresh, actually sent
summaries and applicant confirmations, the new pack uses `CASE_ID/revision-N/` and a distinct
`_revision-N.zip` filename. README/answers identify the version and do not claim government
submission. Registry replacement requires a processed, authorized revision action; changing the
Case version alone cannot authorize it. A prior outbox row cannot attach the new ZIP. Old uncertain
sends can still be reconciled without resend, although revision authorization itself requires them
resolved first. A new revision cannot recall a ZIP already in somebody's inbox.

The local regression includes a clearly synthetic two-correction sequence (budget 2200 → 2600 →
2700), SQLite restart, atomic rollback, chronological retry blocking, fresh delivered-confirmation
binding, independent new ZIP bytes, preserved original ZIP and exact-byte captured delivery.
Separate tests cover versioned receipts, registry authorization and deletion of case-owned archived
artifacts. Six generated PDFs were rendered and visually inspected; no clipping/overlap was found.
These are local fictional/CaptureGmail tests, not ordinary-document accuracy or real redelivery
evidence. Malformed/older chronology, reviewer UI/roles and a real participant journey remain open.
