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
- Only current blocked/intake and confirmation replies are eligible. Older queued replies are
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

The activation-scoped inbox query currently has a 100-message complete-batch limit. Once
exceeded, intake stops explicitly rather than silently skipping old messages. Moving
reconciliation earlier does not remove this limit. Durable incremental intake/backlog handling
is still required for long-running use; increasing the cap alone is not a complete fix.
