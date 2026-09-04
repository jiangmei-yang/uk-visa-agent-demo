# Design System

## Product and architecture contract

The primary user is a reviewer evaluating one sensitive document-preparation case. The observable
outcome is an inspectable `READY_FOR_HUMAN_REVIEW` application pack after—and only after—scope,
evidence, consistency, provenance, policy freshness, and explicit-confirmation checks pass.

The official first surface is email, with `.eml` replay as the credential-free contract and Gmail as
an optional live adapter. The application is a modular monolith divided into platform/API,
application workflow, domain, and infrastructure modules. SQLite stores a canonical Pydantic case
snapshot plus idempotency, outbox, and delivery records. Generated PDFs/JSON/ZIP are derived outputs.

The product excludes visa/ETA advice, other routes, minors, application submission, legal conclusions,
approval prediction and document authenticity decisions. The default review UI uses synthetic
offline data. Gmail integration exists separately; WhatsApp and realistic-material end-to-end
acceptance remain incomplete. See VALIDATION.md for the current evidence and privacy boundaries.

The primary completion event is one pack generated after all current gate checks pass. Guardrails are
zero unsupported-route finalisations, zero packs with an open blocker, zero duplicate side effects on
replay, and zero unprovenanced critical facts.

## Intent

A quiet review desk in bright neutral daylight: precise enough for evidence work, calm enough for
sensitive case review. The visual system is restrained and uses cobalt only as a navigational and
focus anchor.

## Tokens

All colour values are authored in OKLCH.

- Background: `oklch(1 0 0)`
- Surface: `oklch(0.975 0.006 252)`
- Surface strong: `oklch(0.94 0.012 252)`
- Ink: `oklch(0.22 0.025 252)`
- Muted ink: `oklch(0.46 0.025 252)`
- Primary: `oklch(0.478 0.136 251.8)`
- Primary dark: `oklch(0.39 0.12 252)`
- Success: `oklch(0.48 0.11 155)`
- Warning: `oklch(0.55 0.13 72)`
- Danger: `oklch(0.48 0.18 26)`
- Border: `oklch(0.88 0.014 252)`

## Typography

Use the native system sans stack for every product element. Base text is 16px/1.55. Headings use
a compact 1.16 scale and never use display styling. Tabular identifiers and hashes use the native
monospace stack.

## Layout

The assessment surface is one focused case canvas with a compact product bar. The reading order is
fixed: current outcome and proof, an interactive three-email walkthrough, final pack manifest,
prepared case details, then collapsed engineering evidence. At narrow widths every two-column
comparison stacks, while wide technical tables retain labelled local scrolling without causing page
overflow. Main content is capped at 1180px; prose stays under 72ch.

## Components

Use a continuous white canvas separated by rules instead of a dashboard of cards. The current
outcome may use one quiet tinted surface; state badges always combine a label with a shape. The email
walkthrough pairs each real synthetic message with the resulting service decision and preserves the
historical safe stop. Applicant-facing document tables show plain-language lifecycle states. Rule
identifiers, confidence values, and hashes live inside a native disclosure section, where tables
preserve deliberate overflow and accessible full text.

## Interaction model

- The application-pack download is the only primary action and is present only while the current
  delivery gate, held-update check and registered archive-integrity verification pass.
- The offline mode notice remains in main content, not only in a responsive header. A local case
  record is not presented as evidence that a live channel worker is connected or a recipient received a pack.
- The static three-email walkthrough is explicitly a separate recorded fixture and appears only
  for fixture-channel cases. Real cases never inherit Lin's sample story.
- Three keyboard-operable tabs replay the initial pause, correction, and confirmation states.
- A separate **Try the workflow** route lets an evaluator send the three synthetic messages through
  the actual workflow one at a time, with current blockers, passed checks, replies, and pack release
  updated after each action.
- The default view shows the exact first email and safe stop; the current outcome remains visible
  above it so historical state cannot be mistaken for current status.
- The view answers what happened, what changed, and what is ready without requiring domain or
  engineering vocabulary.
- Opening audit details reveals the deterministic gate, provenance ledger, policy version, and
  document hashes without navigating away or interrupting the task with a modal.
- Technical evidence remains present in the DOM and test contract but is not part of the initial
  cognitive load.
- Export and destructive deletion stay below the audit evidence; deletion uses the browser's familiar
  confirmation dialog and exact case-scoped server confirmation rather than a custom modal.

## Motion

Only stateful transitions use motion, 160–220ms with ease-out. Respect `prefers-reduced-motion` and
never delay initial content visibility.
