# Published candidate verification — ab0e69e

Exact source: `ab0e69ea7610d0748d9f958b1729c72b6a3a6b7c` on
`codex/application-records`. This is a candidate, not a main-branch release.
[GitHub run 34080016258](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34080016258)
completed successfully. The job, individual steps and completed logs were inspected.

- Locked dependency installation, lint and typing succeeded.
- Full, unfiltered tests: **5,376 passed**, one Starlette deprecation warning,
  **214.64 seconds** in CI.
- Offline demo succeeded. Five clean stability runs produced the same ZIP hash;
  concurrent console reads passed **100/100**.
- Fresh Docker build and health checks succeeded. Downloaded ZIP passed its
  compressed-data integrity check.
- HTTP smoke checks all passed: initial evidence blockers, no skipping steps,
  duplicate-click state preservation, fresh confirmation after corrections,
  confirmed release, identical repeated downloads, valid CRC, expected customer
  deliverables, no internal audit files, and answers matching the confirmed case.
- Smoke execution recorded **zero model calls and zero mailbox calls**.

Both this run's stability log and HTTP smoke log reported ZIP SHA-256
`e40efd597179eb4bbc49b289ea753a51ed5639abb1ef3fd4304f1e742f22527d`.
This matches the previous candidate's synthetic fixture pack; it does not imply
the conversational changes were absent or that other applicant packs are identical.

This evidence establishes automated candidate checks, not perfect advice or
complete acceptance. Real Gmail recipient-side final delivery, uncoached human
long-conversation evaluation, independent nontechnical installation (including
native Windows), and formal release remain separately unverified. No mailbox
consent, operator review, live-worker deployment, or real dispatch was performed
by this CI run or by reading its logs.
