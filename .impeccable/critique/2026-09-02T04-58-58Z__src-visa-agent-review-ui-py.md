---
score: 40
score_max: 40
p0: 0
p1: 0
method: post-remediation-live-browser
timestamp: 2026-09-02T04-58-58Z
slug: src-visa-agent-review-ui-py
---
Method: post-remediation acceptance review (dual-agent baseline retained; implementation verified independently in the live browser)

# Final UX acceptance — visa preparation Demo

Score: **40/40** on the project-specific interview-Demo rubric. Severity: **P0 0, P1 0, P2 0 blocking release**.

## Ten-dimension result

1. **Immediate product comprehension — 4/4.** The current outcome and a labelled two-minute walkthrough appear in the first viewport.
2. **Email-first workflow proof — 4/4.** All three client messages show sender, time, subject, excerpt, attachments, and exact service response.
3. **Safe-stop clarity — 4/4.** The initial state is visibly withheld with both blockers and an unavailable pack.
4. **Correction clarity — 4/4.** Replacement documents and the invitation before/after date comparison are explicit.
5. **Human-control clarity — 4/4.** Exact final-summary confirmation is visible before release.
6. **Delivery clarity — 4/4.** Pack contents, size, checksum prefix, supporting-document count, and limitations are adjacent to the download.
7. **Engineering-stability proof — 4/4.** The page exposes 8/8 gates, source-linked facts, replay stability, policy version, provenance, lifecycle, and hashes without making them default cognitive load.
8. **Safety invariant — 4/4.** Both UI and download endpoint re-evaluate the current gate; a stored stale pack is rejected with HTTP 409.
9. **Accessibility and responsiveness — 4/4.** Skip link, 16px base type, visible focus, keyboard tab/arrow interaction, 46px CTA, 69px mobile stage targets, reduced motion, labelled overflow regions, and zero root overflow with audit open.
10. **Visual and interaction quality — 4/4.** One focused case canvas, no duplicate navigation, a deliberate calm visual hierarchy, no console errors, and zero Impeccable detector findings.

## Verification evidence

- Desktop root width equals scroll width (1096/1096).
- Narrow-browser root width equals scroll width with the audit open (480/480); wide tables scroll locally.
- Keyboard ArrowRight changes the selected tab and visible panel to Confirmation.
- No browser console warnings or errors.
- 14 automated tests pass; strict type checking and linting pass.
- Stability test passes 20 clean runs and 100/100 concurrent console reads; deterministic ZIP SHA-256 remains `fcea98f0429d3340e22cba89ae9c39b917ce579ae29101aaa0c5c5e7d0e392ea`.
- Rebuilt Docker service is healthy and the verified pack endpoint returns HTTP 200.
- Impeccable detector returns `[]`.

The score is a task-specific completion score, not a claim that usability can never improve after real external-user research.
