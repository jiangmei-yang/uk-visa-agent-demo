# Review-console state clarity — 2026-09-04

Evidence: internal source review, local HTTP/regression tests and the in-app browser's rendered
accessibility tree. This is not independent usability testing or a new pixel/contrast/mobile audit.

## Problems corrected

- The same Lin narrative appeared on every case. The recorded sample is now clearly separate and
  shown only for fixture-channel cases; the current outcome is based on current checks.
- Missing requirements were always labelled Complete. They now say Still needed. Unaccepted
  documents no longer receive a success status, and missing budget is not displayed as £0.
- Ten checks and 10/10 were hardcoded. Current gate count comes from the actual gate; the separate
  historical walkthrough does not pretend to report the current count.
- A passing case gate could show a download button even when the download endpoint rejected a
  held update or altered archive. The page now uses the endpoint's verification before showing it.
- Offline mode was unclear. Main content explicitly says no Email/WhatsApp messages are sent by
  the guided example. Non-fixture records do not claim the channel worker is connected.
- Empty-state copy incorrectly promised restart would restore a deleted case. It now offers the
  separate guided offline workflow without promising restoration.

## Verification and limitations

The full local suite passes 332 tests, lint and typing. Added tests cover incomplete Gmail records,
missing archive verification and empty-state instructions; existing held-update/tampered-archive
endpoint tests also check that the page has no download link. The completed fixture still offers
the verified ZIP. Docker was rebuilt using the existing named runtime volume, not reset; its
health check passed. Browser text showed the offline notice, separate sample label, current
download and no recipient-delivery claim. The existing fixture's displayed archive hash stayed
`8bc0681a8374…` across deployment.

The UI still does not provide a live inbox, channel-worker health panel, recipient delivery UI,
authenticated operator workflow, or independently validated ease of use. It also still contains
a historical fixture conversation rather than a rendered message history for arbitrary cases.
These limitations remain part of the acceptance backlog, not reasons to declare the UI complete.
