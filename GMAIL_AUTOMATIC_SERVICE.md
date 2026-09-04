# Controlled automatic Gmail service

The earlier Gmail experiments were manually driven. `prepare --watch` does not send replies.
The new `serve --watch` mode prepares and sends bounded, deterministic service replies for one
explicitly registered sender, with ordinary subjects. It is not open public intake.

```bash
uv run python scripts/gmail_sandbox.py serve \
  --sender applicant@example.test --mailbox service@example.test \
  --after ACTIVATION_UNIX_SECONDS --state-dir data/registered-applicant \
  --watch --interval 60
```

Replace both addresses and the activation timestamp. Supply the service mailbox's existing Gmail
authorization and DeepSeek configuration as described in `GMAIL_SANDBOX.md`. Run first in a dedicated
test mailbox. The sender, optional subject and activation boundary are bound to the state directory;
do not switch scope by deleting its database. Use a separate state directory for a new registration.

## Sending boundary

- Exactly one parsed recipient must match the registered sender, including display-name addresses.
- Fixed subjects and test markers are not required. Existing mail before activation is excluded.
- Auto-Submitted, bulk/list mail and mailing-list headers are ignored to avoid responder loops.
- Bounded extraction produces case facts; automatic outward wording uses reviewed deterministic
  templates, not an unrestricted model-written reply.
- Current blocked/intake, confirmation and retained post-pack update receipts are eligible; receipts
  do not reopen a case or deliver a revised pack (see `HUMAN_REVIEW_RECOVERY.md`). Older queued replies are
  withheld. Final `ready` replies stay pending for explicit reviewed dispatch; no pack is auto-sent.
- Existing uncertain-send reconciliation and deduplication remain in force.
- Uncertain sends are reconciled before inbox listing or model processing. An intake failure
  still prevents dispatch of queued replies, but no longer prevents observing previous sends.
  Runner integration tests cover an oversized inbox and a listing timeout with simulated
  providers; these are not claims about real Gmail outages.
- `worker_status.json` records the process ID, last cycle timestamp and polling/idle/error state.
  It is observational evidence only: confirm process liveness separately. Iteration failures record
  only the error class, wait the normal interval and do not reset delivery history.

## Local supervised deployment evidence, 2026-09-04

A macOS LaunchAgent `com.visa-agent.gmail-user` was installed for the owner's additional test mailbox,
with a 60-second interval, no subject restriction, an explicit activation boundary and private
state/logs under `data/gmail-live-user`. `launchctl print gui/501/com.visa-agent.gmail-user` confirmed
the process running. This is a logged-in Mac service: sleep/offline conditions interrupt polling;
it is not an always-on hosted production deployment.

The owner's ordinary enquiry was processed and automatically replied to. Gmail's authenticated
thread API confirmed one outbound message with the expected recipient and subject and
`Auto-Submitted: auto-replied`. This proves provider-side acceptance, not observation of the
recipient's university inbox. The first attempt failed locally because the display name was
mistakenly compared to a bare address; no provider send occurred. That failure was retained privately,
the address check was corrected, and only that proven pre-send rejection was re-queued.

To stop this installed service: `launchctl bootout gui/501/com.visa-agent.gmail-user`.
To start it again: `launchctl bootstrap gui/501 "$HOME/Library/LaunchAgents/com.visa-agent.gmail-user.plist"`.
Do not run a second worker against the same state directory; the state lock rejects overlap.

On 2026-09-04 at 08:19 UTC the supervised worker was restarted to load the multi-stage
conversation pacing changes documented in `CONVERSATION_REVIEW.md`. A new live process and
completed idle cycle were verified; the existing enquiry was recognized as a duplicate and
automatic dispatch was empty. No old reply was resent. This verifies code reload and duplicate
handling, not recipient-side observation of a new conversation using the revised wording.

## Not yet complete

Open onboarding from arbitrary senders, explicit privacy/processing consent, abuse limits,
public-service deployment and automatic final-pack release are not implemented by this mode.
Do not describe this registered-sender rollout as a fully public autonomous adviser.

Automatic `serve` now uses durable incremental discovery instead of the lifetime 100-message
full-batch limit. It follows bounded pages per cycle, processes at most 100 bodies per cycle,
and withholds dispatch until the backlog is drained. Manual `prepare` still has its bounded
100-message trial scope. Implementation, migration evidence and remaining limitations are in
`GMAIL_INCREMENTAL_SYNC.md`; live large-backlog/expired-history tests are still outstanding.

## Authorization interruption and recovery — local runner tests

`tests/integration/test_gmail_auth_recovery.py` invokes the actual `run_once` service path with
injected authorization failures at credential construction/refresh, profile verification, and
initial discovery. Each case starts with a persisted applicant and one pending reply. All three
assert that the failed iteration leaves the case snapshot, outbox payload/status and processed
event count unchanged, with no sender call. After removing the injected failure, two further
iterations produce exactly one provider-bound reply and one recorded SENT row.

These are local simulations using an empty fake inbox and a capture sender. They do not revoke,
refresh, or repair a real token and do not cover a send rejected after its attempt was recorded.
The separate historical `eval_output/gmail_invalid_auth_2026-09-04.json` is real read-only HTTP
401 evidence only; it must not be combined with these simulations into a claim of live OAuth
recovery. Real account reconnection and recipient-side observation remain required. The full
local regression suite after adding these tests passes 335 tests, with lint and typing passing.
