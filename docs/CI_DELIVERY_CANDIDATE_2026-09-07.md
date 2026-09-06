# Delivery-candidate CI verification

[Run 34062260806](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34062260806)
completed successfully at exact head
`b3c391aea73161ed262ddb43455d21d83d9b2f2d`. Job status, individual step
conclusions and the relevant completed test/stability/container logs were read.
This supersedes the earlier candidate run for these source changes, not its
historical evidence.

Observed checks:

- Locked dependencies, full lint and configured typing passed.
- `make test` invoked `uv run pytest` without exclusions: **5,153 passed**, one
  existing Starlette warning, **223.92 seconds**.
- The offline demo completed successfully.
- Five clean stability runs produced one ZIP hash; concurrent console reads
  passed **100/100**.
- Docker Compose built and reached health in the fresh runner. Downloaded ZIP
  compressed-data integrity passed.
- The fresh-container smoke report returned `all_passed: true`, including initial
  evidence blocking, duplicate-click state preservation, fresh confirmation after
  correction, release of the confirmed pack, byte-identical repeated download,
  valid CRC, customer deliverables, exclusion of internal audit files and matching
  the confirmed case. It recorded zero mailbox and model calls.
- Cleanup and all post-job steps succeeded.

The stability and smoke ZIP SHA-256 was
`e40efd597179eb4bbc49b289ea753a51ed5639abb1ef3fd4304f1e742f22527d`.
It differs from the earlier candidate because the PDF content/rendering changed;
this is not presented as identical bytes across different source versions.

The Gmail worker was independently observed running during this check, with
unknown processing consent and nine SENT records. No new consent or recipient-side
delivery was observed. It was not restarted to import these PDF changes. Local
setup checks found the configured Gmail/DeepSeek files and Python modules, but
file presence does not validate authorization or consent. WhatsApp credentials
remain unconfigured, consistent with the deferred live-device scope.

The nontechnical walkthrough now explicitly identifies the development branch,
because downloading default `main` would obtain the older source. These later
documentation-only edits do not alter the tested runtime. Native Windows and
uncoached interviewer validation remain unproven; no main merge, release, public
intake enablement or final Gmail dispatch occurred here.
