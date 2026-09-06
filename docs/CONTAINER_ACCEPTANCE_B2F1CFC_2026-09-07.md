# Fresh container acceptance: b2f1cfc

This is an isolated execution check of current code, not a live Gmail, launcher
GUI, Windows-installation or independent interviewer-usability acceptance.

- Source: `b2f1cfc`, clean at build start.
- Image: `sha256:e56076b6e3d8a6edc77db0d06f6336d35fd6ce8035135c30735feb9a5d113200`.
- Container: `visa-agent-acceptance-b2f1cfc`, non-root `appuser`, no mounts.
- The image was built with the repository Dockerfile and frozen uv lock. Docker
  used its legacy builder (deprecation warning), not a claimed BuildKit build.
- Synthetic startup/database/output were isolated inside this container. The
  existing `127.0.0.1:8000` console and host Gmail data were not changed.
- Probe port: 32770; Docker assigned 32771 after restarting this container.

The real HTTP lab probe passed all 12 checks, including initial conflict and
missing-translation gates, refusing out-of-order confirmation, requiring explicit
confirmation after replacement evidence, ZIP structure and CRC, and supporting
documents. Raw results are retained in
`eval_output/container_acceptance_2026-09-07-b2f1cfc.json`.

Before and after restart, the downloaded ZIP SHA-256 was exactly
`95471ed515aa3d3d0f75fc0ae23893cbe6805a1ff33bea459c01ad1d0eb0d72d`.
The container became healthy again. This verifies restart persistence within its
writable layer, not recreation/persistence of a Compose named volume.

## Document review findings: release still incomplete

The actual six generated cover documents were downloaded and rendered for visual
inspection, not reconstructed from expected fixtures. The local renderer emitted
fontconfig warnings; rendered pages were inspected rather than assuming success.
The student summary exposes employer fields as “Not provided”. This needs explicit
applicability formatting; it should not imply that this student omitted required
employer details. The summary separately says travel-history/contact completeness
has not yet been confirmed. That remains an honest unfinished review state, not
evidence of a fully completed official application form.

The cover-letter draft is still mechanically phrased (for example, “My recorded
occupation status is student” and a generic employer-or-school funding category).
It is marked as a draft needing source review, but should not be presented as
polished, ready-to-submit consultant writing. These observations are failed/open
editorial acceptance items, even though the HTTP probe passes.

No human approval, provider call, real mailbox message, GitHub push or main-service
deployment occurred. Nontechnical operator review, full applicability/document
QA and current-code recipient-side Gmail final delivery remain outstanding.
