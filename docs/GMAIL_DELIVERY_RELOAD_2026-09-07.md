# Tested delivery formatter loaded into the registered Gmail worker

The registered worker was reloaded after [candidate CI](CI_DELIVERY_CANDIDATE_2026-09-07.md)
completed successfully. Before stopping it, the diff against tested head
`b3c391aea73161ed262ddb43455d21d83d9b2f2d` was empty for `src`, the Gmail runner,
`pyproject.toml` and `uv.lock`. Local head `2eef1c0` added documentation only.

## Observed procedure

- Exact LaunchAgent `com.visa-agent.gmail-user` was inspected, then stopped.
  The former PID 21977 was verified absent before proceeding.
- SQLite backups of the case and sync databases were created under the existing
  private state directory; both integrity checks returned `ok`.
- The same plist was bootstrapped without widening sender registration, changing
  mailbox, activation cutoff, interval or state directory.
- New PID 69511 was independently observed running. Its completed idle cycle was
  recorded at `2026-09-06T21:56:37.355135+00:00` (September 7 Hong Kong).
- Bidirectional SQL `EXCEPT` comparisons against the backup returned zero changed,
  added or removed rows for each of `cases`, `outbox` and `processing_consent`.
  This verifies those complete rows, not merely unchanged counts.

These are dated observations, not a continuous uptime guarantee. Private backups,
mail contents and credentials are not added to the repository. The main Docker
console was not rebuilt or reset.

No applicant grant, operator approval, manual retry, historical replay or manual
message was generated. The registered applicant's current processing permission
remains unknown. Automatic final `ready` delivery is still excluded. This reload
loads the tested PDF fixes; it does not prove a fresh real-recipient conversation,
consent, reviewed final-pack dispatch or ZIP receipt.
