---
score: 27
score_max: 40
p0: 1
p1: 5
method: dual-agent
timestamp: 2026-09-02T04-46-40Z
slug: src-visa-agent-review-ui-py
---
Method: dual-agent (A: independent senior product-design review · B: independent detector and live-browser evidence review)

# Baseline critique — visa preparation review UI

Score: **27/40** (Nielsen heuristics). Severity: **P0 1, P1 5, P2 4, P3 1**.

## Outcome

The current page is a restrained and credible review console, but it is not yet an excellent interview Demo. It summarizes the finished case instead of letting a nontechnical evaluator inspect the defining behavior: the real client email, the exact contradiction, the service's correction request, the deliberately withheld pack, the replacement evidence, explicit confirmation, and only then delivery.

## Must fix

1. **Bind download to the current safety gate.** The view exposes the ZIP whenever `case.delivery_path` exists, while readiness is separately derived from `gate.allowed`. The download endpoint also serves the stored file without re-evaluating the current gate. Revalidate both presentation and endpoint so a stale or currently blocked pack is withheld.
2. **Turn the abstract timeline into an inspectable email story.** Show sender, subject, excerpt, attachments, service decision, exact request, and state change at each of three checkpoints.
3. **Preserve the historical safe stop.** The first checkpoint must be amber/blocked with two explicit blockers and an unavailable pack. Only the final state should be green and downloadable.
4. **Connect delivery to proof.** Beside the CTA show 8/8 gate checks, policy freshness, source-linked facts, deterministic replay, pack contents, active/superseded counts, and checksum/creation metadata.
5. **Fix mobile audit reflow.** At 374px, opening audit increases root scroll width from 354px to 514px. Add the missing min-width constraints and preserve overflow locally.

## Should fix

- Remove duplicate rail and sticky navigation for a single-case demo; provide one clear walkthrough control.
- Add a skip link, 16px base type, at least 44px primary/touch targets, labelled focusable table overflow, and an accurate disclosure state.
- Use one human-facing term: “Ready for human adviser review”; keep machine enums technical-only.
- Explain “8 active + 1 superseded” wherever document counts appear.
- Provide meaningful empty, stale-policy, blocked, download-success, and download-error states.
- Make the empty state actionable without requiring knowledge of a separate launcher.

## Browser evidence

- Desktop 1440×900: no global overflow, sticky anchors work, no console errors; page height 2586px and duplicated navigation create scanning burden.
- Mobile 374×812: closed page has no root overflow; audit-open state creates 160px root overflow. Sticky nav clips its final item but can scroll locally. Document table correctly scrolls locally.
- Body is 15px; secondary labels and tables are 10–13px. CTA height is 42px.
- Focus rings, semantic landmarks, native details, reduced-motion support, and meaningful contrast are good.
- Impeccable detector returned `[]`; runtime testing found the issues above. Overlay injection was intentionally skipped for the read-only assessment; evidence came from a fresh browser tab, DOM/a11y snapshots, computed geometry/styles, responsive overrides, native interactions, and console logs.

## Excellent-state acceptance criteria

A first-time evaluator can understand the full blocked→corrected→confirmed→delivered story in 90 seconds, inspect the exact email/evidence at every step, see that download is impossible when any gate fails, understand what is inside the pack, and operate the page at 375px, 200% zoom, and keyboard-only without loss of information or control.

Questions skipped: the user explicitly requested autonomous remediation and continuous iteration to completion.
