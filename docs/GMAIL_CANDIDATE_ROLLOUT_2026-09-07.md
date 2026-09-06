# Gmail candidate reload — 2026-09-07

The registered, supervised Gmail worker was restarted with the production source
covered by [candidate CI 34060825442](CI_CANDIDATE_2026-09-07.md).
Before restarting, `git diff 3e2380c5b618662a2a3bda540dc8bed263920dc7 -- src
scripts/gmail_sandbox.py pyproject.toml uv.lock` was empty. Local head was
`b24b8e4`; its additional change was documentation, not runtime code.

## Observed procedure and result

- Read-only inspection found nine SENT outbox rows and one unknown processing
  consent record. There were no pending or uncertain-send outbox rows.
- The exact registered LaunchAgent was stopped with `launchctl bootout`, and
  its former process was confirmed absent. No second worker was started.
- Both the case database and incremental-sync database were backed up using
  SQLite's backup command under the existing private, ignored state directory.
  Both backups returned `ok` from `PRAGMA integrity_check`.
- The existing LaunchAgent configuration was bootstrapped unchanged. Its sender,
  mailbox, activation boundary, state directory and 60-second interval were not
  widened. No database reset, consent grant, manual retry or manual send occurred.
- New PID 21977 was independently observed running. Its status file recorded an
  idle completed cycle at `2026-09-06T21:31:50.818321+00:00` (September 7 in Hong
  Kong). The case database still showed nine SENT messages and unknown consent.

The PID and timestamp are historical observations, not ongoing uptime guarantees.
The main Docker UI was not rebuilt or reset during this operation.

## Next real-recipient checkpoint

The registered participant must initiate an ordinary enquiry, receive the current
processing notice and personally grant permission using the notice reference in
the same thread. No personal attachment is needed for that first enquiry. Chat
approval, OAuth and registration must not substitute for the email consent ledger.

After a real grant, verify ordinary follow-ups and deferred facts before moving
to fictional attachments, correction, explicit review, renewed customer
confirmations and recipient-side ZIP inspection. Final `ready` dispatch remains
excluded from the automatic worker. Do not treat this successful idle reload as
new Gmail delivery, applicant consent, recipient observation or full acceptance.
