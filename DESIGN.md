# Design System

## Product and architecture contract

The primary user is a reviewer evaluating one sensitive document-preparation case. The observable
outcome is an inspectable `READY_FOR_HUMAN_REVIEW` application pack after—and only after—scope,
evidence, consistency, provenance, policy freshness, and explicit-confirmation checks pass.

The official first surface is email, with `.eml` replay as the credential-free contract and Gmail as
an optional live adapter. The application is a modular monolith divided into platform/API,
application workflow, domain, and infrastructure modules. SQLite stores a canonical Pydantic case
snapshot plus idempotency, outbox, and delivery records. Generated PDFs/JSON/ZIP are derived outputs.

The MVP excludes visa/ETA advice, other routes, minors, application submission, legal conclusions,
approval prediction, real-person data, WhatsApp, production OCR, and document authenticity decisions.

The primary completion event is one pack generated after all eight gate checks pass. Guardrails are
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

The desktop console uses a 248px case rail plus a flexible detail column. At 820px the rail becomes
a horizontal case summary and tables gain deliberate horizontal overflow. Main content is capped at
1440px; prose stays under 72ch.

## Components

Use flat, bordered sections with 12px radii and no decorative shadow. State badges always combine a
label with a shape or icon. Tables keep headers visible, hashes wrap or truncate with accessible full
text, and blocker panels use a full tinted background rather than a coloured side stripe.

## Motion

Only stateful transitions use motion, 160–220ms with ease-out. Respect `prefers-reduced-motion` and
never delay initial content visibility.
