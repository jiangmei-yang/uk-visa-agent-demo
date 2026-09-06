# Verified candidate CI

Run [34060825442](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34060825442)
completed successfully for `codex/application-records` at exact commit
`3e2380c5b618662a2a3bda540dc8bed263920dc7`. This is the candidate run, not the
previous main-branch result. Job status and the completed logs were both read.

Observed checks:

- Locked dependency installation, full lint and configured type checking passed.
- `make test`: **5,124 passed**, one existing Starlette warning, 202.92 seconds.
  No test deselection arguments were used.
- `make demo` succeeded.
- Stability: 5 clean runs produced the same ZIP SHA-256; concurrent console
  reads passed 100/100.
- Docker Compose built and became healthy in the fresh runner, and a downloaded
  ZIP passed its integrity check.
- The fresh-container HTTP smoke probe passed all 12 checks: initial blocking,
  out-of-order and duplicate-click protection, post-correction confirmation,
  consistent repeated download, CRC, expected deliverables, internal-audit
  exclusion and matching the confirmed synthetic case.
- Cleanup and post-job steps succeeded.

The stability/smoke ZIP SHA-256 reported by this run was
`1a4fdbaa023abed6c442374b873f87743fc2173d4753714c4f2b622b29fd4bf9`.
The smoke report explicitly recorded zero model calls and zero mailbox calls.

This is a reproducible engineering checkpoint, not final product acceptance.
Main was not merged, the live Gmail worker was not restarted, no real message or
operator/customer approval was generated, and no release was published. Real
recipient-side Gmail delivery, independent unscripted/user usability, authenticated
guided operator review and remaining document/interaction quality still require
separate evidence. Later code changes are not covered by this exact run.
