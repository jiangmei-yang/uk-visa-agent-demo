# Pack materialization and Gmail intake recovery

Updated 2026-09-04. This work closes specific local recovery gaps; it does not complete
ordinary-material final-delivery acceptance. Overall test totals, CI and deployment status are
recorded in [VALIDATION](VALIDATION.md), not inferred from the focused counts below.

## Materialization is not an acknowledgement or a send

[Pack generation](src/visa_agent/delivery/pack.py) now checks a returned, already-registered ZIP
against the current case path/revision, permitted output directory, file availability and stored
SHA-256. A missing, changed or inconsistent registered archive is withheld, not regenerated.
A registered current revision with a missing case path is also withheld. A legitimate next
revision retains its intact predecessor; the existing processed operator-authorization check in
`save_delivery` remains authoritative.

Each new attempt renders into its own private staging directory. Only currently accepted
supporting documents are copied, and the final staged bytes are checked against their recorded
document SHA-256 before registration or publication. Missing or changed bytes fail the attempt;
the accepted record is not rewritten to make them match. Generated PDF layout is unchanged.
All accepted filenames are independently checked before any supporting-file copy: blank names,
`.`/`..`, slashes, backslashes and absolute paths are rejected, not normalized into a different
accepted record. This boundary does not assume persisted state always passed inbound sanitization.

Case, audit, support and ZIP destinations are resolved inside the configured output directory
before mutation. An unsafe case identifier, escaping symlink, colliding registered ZIP or
registered archive within the target case tree is rejected. Existing unregistered partial
case/ZIP output is moved into an output-local `.unregistered-pack-*` directory, not merged into
the retry or deleted. Automatic cleanup is limited to the attempt's own `.pack-stage-*` directory.
Quarantined evidence has no automatic retention/deletion policy in this change.

SQLite serializes materialization/registration against persisted preparation controls. Handled
failures roll back registration and the case snapshot. Publication can have moved some files
before a later failure; those unregistered remnants are isolated on retry. This is recoverable
publication, **not** an atomic transaction across SQLite and the filesystem. A caller using the
normal top-level generation entry point receives its new in-memory ready path only after that
transaction succeeds.

## Runner scheduling and failure boundaries

In automatic `serve`, [the Gmail runner](scripts/gmail_sandbox.py) separates candidates whose
workflow event is already committed from newly unprocessed events. New events retain provider
receipt-time order and run first; committed recovery work follows. Both lists share one
**100-message-body per cycle** budget. This is not a cap on metadata requests or total discovery
work, and it is not a global oldest-first ordering across both lists. Manual `prepare` keeps its
existing bounded trial discovery behavior.

A required pack returning `None` or raising an exception is not acknowledged. The runner keeps
its candidate pending, continues the bounded intake batch so later pauses/corrections can be
retained, and then raises a generic `PackPreparationError` with no automatic dispatch that cycle.
Private materialization exception detail is not included in that error. Workflow exceptions
outside pack generation still stop immediately; they are not reclassified as pack failures.

On a later cycle, an already-committed event goes through workflow deduplication, not a second
extraction or a second outbox insertion. If a later accepted pause/correction has invalidated the
unmaterialized final confirmation, the duplicate may be acknowledged without reviving the old
pack. All remaining discovery candidates must still drain before automatic dispatch. Existing
source, consent, pause, held-update, revision and outbound authorization checks are not waived.
The automatic sender does not release final ZIPs; fixture-captured follow-up replies in these
experiments are not recipient delivery evidence.

## Measured local failures and regressions

### Final-reply authority after a correction

An additional isolated workflow reproduced four send defects: after a failed materialization and
subsequent correction/reconfirmation, an old `ready` reply could carry the new ZIP and another reply
could send it again; a different final reply could also send while the same revision already had
`SENDING`, `AMBIGUOUS` or `SENT` evidence. A fixture setup omission was corrected before measuring
the production baseline of **4 failed / 2 passed**. The final
[nine regressions](tests/integration/test_ready_reply_authority.py) pass.

The dispatcher now uses the latest persisted `ready` row within the same case/revision as the
final-confirmation event authority. SQLite rowid distinguishes same-second commits; arbitrary later
conversation and wall-clock order do not replace that authority. Unattempted older final replies
are retired only when a newer final reply exists, without increasing send attempts. Preflight checks
current final confirmation/materialization and any other final-reply attempt in the revision.
Existing `SENDING`, `AMBIGUOUS` and `SENT` evidence remains untouched. An obsolete, claimable `RETRY`
may become `FAILED`, retaining its attempt/provider fields; this is not permission to resend.
An unrelated FAQ does not revoke a still-valid final reply, and an explicitly authorized new revision
can be delivered independently.

This is **event-level authority, not a persisted final-summary-fingerprint binding**. No schema or
workflow confirmation gate was loosened. Three existing transport-only tests needed their explicitly
simulated final-confirmation precondition set: the new check correctly rejected their former
`READY`-but-unconfirmed setup. The separate new workflow regression uses actual fixture confirmation
turns and captured prior sends. All sends here are local captures, not real Gmail acceptance.

### Pack and intake experiments

These are exposed, deterministic regressions, not holdout scores or reliability percentages.

| Experiment | Initial result and interpretation | Later evidence |
|---|---|---|
| Materialization integrity/retry | 6 failed / 8 passed against the prior implementation: missing/changed archives, wrong archive hash, outside-root registered path, lost case path, and stale retry support bytes | [38 tests](tests/integration/test_pack_materialization_recovery.py), expanded to publication/commit failures, path boundaries, filename validation and source integrity |
| Historical archive in a replaced case subtree | 1 failed / 1 passed in the added collision pair before its guard | Target ZIP, target case path and case-subtree collisions are now covered |
| Accepted source bytes | 2 failed / 2 passed before the SHA check: changed source and changed staged copy were not rejected; missing source already failed; the clean index/ZIP check already passed | Missing/changed source and changed-copy rejection preserve acceptance; every packed support file matches its recorded/indexed SHA |
| Persisted accepted filenames | 8 failed before independent filename validation; a copy spy stopped the old implementation before writing any support bytes | Empty/whitespace, dot/dotdot, traversal, nested, backslash and temporary absolute-path cases are rejected before any support copy; sources, acceptance and unrelated temporary files remain unchanged |
| Runner pack/acknowledgement window | An initial harness had 5 setup failures because it marked an unclaimed outbox row `SENT`; these were not product failures. After correcting the seed, the old runner baseline was 1 failed / 4 passed: a `None` pack was acknowledged; I/O failure and three process-exit windows already passed | [13 runner tests](tests/integration/test_gmail_pack_recovery.py) cover pending recovery, failure isolation, later updates, saturation and trusted-original restore |
| Saturated committed recovery queue | 2 failed before new-event prioritization: 100 committed candidates trapped a later new pause/correction behind recovery work | Both variants process the new update first while keeping the shared body cap and backlog dispatch stop |
| Broken pagination to operator rescan | [2 journey tests](tests/integration/test_gmail_rescan_journey.py) passed on their first run; no production fix was needed for these new experiments | Broken page, preserved candidates, audited rescan, fresh-anchor failure and overlap catch-up through the actual runner |

The runner/rescan subset passed **15 tests**. The materialization-related subset passed **148**
at its freeze, including **38** new materialization tests and existing control/revision/lab
regressions; these focused counts are not the complete suite. See [VALIDATION](VALIDATION.md)
for the current full count and CI result.

The clean synthetic fixture ZIP was compared with the pre-change `HEAD` materializer:
**20,969 bytes**, byte-identical, SHA-256
`7e27fe057a2643dd892bd41e94ed47245a225ff716bb99a7a4802bb0447b641f`.
This protects that fixture's byte determinism, not all platforms or arbitrary applicant documents.

## What the recovery experiments actually exercised

The pack/runner tests use generated fixture PDFs, temporary SQLite stores, scripted extraction
and fake Gmail components. Prior `SENT` replies are explicitly simulated setup, not provider
acceptance. Three tests really terminate a child process with `os._exit(75)` after workflow commit,
after pack commit or after journal acknowledgement, then reopen state. They check duplicate-free
workflow/outbox recovery and unchanged committed ZIP bytes; they do not cut machine power.

The rescan journey uses the actual Gmail adapter, runner, journal, operator command and dispatcher
with a fake provider. Its first page is committed before a synthetic page-two rejection. The
audited operator command preserves candidate/case/binding state; a failed fresh-anchor read keeps
the rescan requested. Two overlapping full pages plus history catch-up discover 103 unique
messages, including one arriving during discovery. Runner/services/stores are recreated between
iterations: the first body batch processes 100, the next processes three, and an idle iteration
does no new body/model/send work. One captured newest reply replaces 102 obsolete unsent replies.
This test reloads the runner; it does not restart a live Gmail worker or induce a real provider fault.

These isolated regressions used no external API/model request, real personal document, credential
access or live-state mutation. Deployment checks are recorded separately in VALIDATION.
Earlier real Gmail observations remain separately bounded in
[GMAIL_LIVE_EVIDENCE](GMAIL_LIVE_EVIDENCE.md) and
[GMAIL_INCREMENTAL_SYNC](GMAIL_INCREMENTAL_SYNC.md). In particular, a healthy live rescan and
these fake broken-page tests do not combine into proof of live provider-fault recovery.

## Recovery limits and remaining acceptance

- A damaged registered archive needs its **trusted original bytes**, matching the existing hash
  and registered path. The fixture test restores its retained original byte string, then requests
  an audited human revision and verifies renewed confirmation is required. There is no automatic
  archive reconstruction, registry-hash rewrite, backup provenance service or general restore UI.
  Do not delete databases, erase delivery history, or substitute newly generated bytes to bypass
  this boundary. A real operator recovery/revised-pack delivery journey remains unproved; see
  [HUMAN_REVIEW_RECOVERY](HUMAN_REVIEW_RECOVERY.md).
- Continuous arrival of at least one full body budget of new messages can defer committed pack
  retries indefinitely. Finite-backlog tests prove the recorded scheduling cases, not fairness or
  bounded completion under unlimited new traffic. Persistently unavailable candidates can also
  block draining/dispatch; rescan does not fabricate their contents or acknowledge them.
- No power-loss/filesystem-durability guarantee, hostile local-writer defense, real provider
  pagination failure recovery, or ordinary-material final-delivery proof is supplied. Source SHA
  consistency means the packed bytes match accepted bytes; it does not authenticate a passport,
  establish document truth, or turn a synthetic identity summary into acceptable identity evidence.
- A consenting participant's ordinary documents, final confirmation, controlled release and
  recipient-visible ZIP remain separate acceptance work. Independent nontechnical observation
  is also still required. Passing fixtures or more automated tests cannot replace either.

Re-run the new isolated files without external service credentials:

```bash
uv run --no-sync pytest tests/integration/test_pack_materialization_recovery.py \
  tests/integration/test_gmail_pack_recovery.py tests/integration/test_gmail_rescan_journey.py
```
