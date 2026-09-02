# UK Visa Agent Demo

A constrained, email-first UK Standard Visitor visa document-preparation agent: natural language
at the edges, deterministic rules and delivery gates at the centre.

> Assessment software using synthetic data only. It does not give legal advice, decide eligibility,
> submit an application, or predict an outcome.

## What the demo proves

The featured case is an adult PhD student applying from Hong Kong to attend a London conference.
The first delivery attempt is blocked by an invitation/trip date conflict and a missing certified
translation. A later email supplies corrected documents; a final email explicitly confirms the
summary. Only then does deterministic code generate a `READY_FOR_HUMAN_REVIEW` package.

The language model is never the control plane. It may propose schema-bound facts and draft text,
but it cannot select requirements, mutate workflow stages, clear issues, or authorise delivery.

## Easiest path for an interviewer

No Python, API key, Gmail account, or terminal knowledge is required. Install and open
[Docker Desktop](https://www.docker.com/products/docker-desktop/), then:

- macOS: double-click `START_DEMO.command` (and approve opening it if macOS asks).
- Windows: double-click `START_DEMO_WINDOWS.bat`.

The launcher builds the app, prepares the complete synthetic case, waits until the health check
passes, and opens <http://127.0.0.1:8000>. Use the matching `STOP_DEMO` file when finished. See
[START_HERE.md](START_HERE.md) for the one-page walkthrough and troubleshooting.

The first launch normally takes a few minutes because Docker downloads the base image. Later
launches reuse it. All data is synthetic and stays inside the local Docker container.

## Developer path

Requirements: `uv` and Python 3.12 (uv installs the project interpreter automatically).

```bash
make setup
make demo
make web
```

Open <http://127.0.0.1:8000>. The complete demo needs no network, Gmail account, OAuth token, or
OpenAI API key.

Expected `make demo` behaviour:

1. Generate synthetic PDFs and replay three committed `.eml` fixtures.
2. Block finalisation after the first email and retain the reasons.
3. Resolve the seeded issues through replacement/translation evidence.
4. Wait for explicit final-summary confirmation.
5. Generate the review ZIP and audit artifacts.
6. Replay every email again and prove case/event/outbox/delivery counts do not change.

Inspect [demo_output/demo_report.json](demo_output/demo_report.json) after running the demo.

## Delivered package

```text
visa_application_pack_<case_id>.zip
├── 00_READ_ME_FIRST.pdf
├── 01_case_summary.pdf
├── 02_personalised_document_checklist.pdf
├── 03_document_index.pdf
├── 04_cover_letter_draft.pdf
├── 05_application_answers.json
├── 06_open_issues.pdf
└── supporting_documents/
```

An adjacent `audit/` directory contains the full case snapshot, evidence ledger, rule evaluations,
and the final gate result. Generated answers come from structured state and active evidence—not a
model's recollection of the email thread.

## Supported scope

- Adults 18+ who have already decided to prepare a Standard Visitor visa application.
- Tourism, family/friend visits, business activities, and conferences.
- Employed, student, and self-employed applicants.
- Self, employer/school, or personal-sponsor funding.
- English/Welsh evidence and non-English evidence with certified translations.

The workflow abstains or requires human review for route/ETA determination, minors, other visa
routes, medical/marriage/transit/paid-engagement/long-academic visits, serious immigration or
criminal history, unreadable critical evidence, unresolved contradictions, and stale policy.

## Architecture

```text
.eml fixture / Gmail
        │
        ▼
typed InboundEvent ── idempotency key
        │
        ▼
bounded extraction proposal ── Pydantic schema
        │
        ▼
workflow service ── evidence ledger ── SQLite snapshot + transactional outbox
        │
        ├── versioned policy rules
        ├── deterministic consistency checks
        └── allow-listed state machine
                    │
                    ▼
             delivery gate
                    │ explicit applicant confirmation
                    ▼
           deterministic PDF/JSON/ZIP
```

This is a modular monolith. Domain code depends on neither FastAPI nor Gmail/OpenAI SDKs. Provider
adapters sit behind narrow interfaces. See [DESIGN.md](DESIGN.md) and
[RELIABILITY.md](RELIABILITY.md). The model and workflow decision, alternatives, and evaluation gate
are recorded in [ADR-001](docs/adr/001-agent-and-workflow.md).

## Commands

```bash
make setup       # install runtime + development dependencies
make demo        # credential-free deterministic replay
make test        # unit, contract, adversarial, golden and integration tests
make lint        # Ruff
make typecheck   # strict mypy
make stability   # repeated clean runs + concurrent review-console reads
MODEL=<model-id> make agent-eval-live  # optional paid synthetic model evaluation
make web         # local case-review console
make start       # build and start the complete Docker demo
make stop        # stop the Docker demo
make clean       # delete disposable local demo data
```

## Optional live providers

Install optional provider SDKs with `uv sync --extra dev --extra live`.

- `OpenAIStructuredLLM` uses a Pydantic schema through the Responses API. The model ID must be chosen
  explicitly after evaluation. It returns proposals only; the workflow grounds, validates, and
  applies them behind a mandatory bounded guard.
- `GmailAdapter` accepts an OAuth-authenticated Gmail API service, polls a configured query, preserves
  thread headers, downloads attachments, and can send replies/ZIP attachments. Follow Google's
  Python OAuth quickstart and use least-privilege scopes. Never commit `credentials.json` or
  `token.json`.

Live mode is intentionally not part of the default assessment path. WhatsApp is deferred until the
mandatory email path and safety gates are validated.

## Policy sources

The snapshot is checked on 2026-09-02 and versioned as 2026-02-25, matching the current update date
shown by GOV.UK at implementation time. Rules contain source metadata and a review deadline. The
synthetic replay freezes its evaluation clock at 2026-09-02 so the assessment remains reproducible;
live/API readiness checks use the actual current date and block after the review deadline.

- [Standard Visitor application information](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa)
- [Official supporting-document guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
- [Gmail API Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
- [Gmail attachment uploads](https://developers.google.com/workspace/gmail/api/guides/uploads)
- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)

No universal three- or six-month bank-statement rule is invented. Flight and hotel bookings are not
treated as core blockers. Providing documents does not guarantee a successful application.

## Repository map

```text
src/visa_agent/domain/      models, state machine, rules, gate
src/visa_agent/workflow/    event orchestration
src/visa_agent/channels/    fixture and Gmail boundaries
src/visa_agent/llm/         deterministic and OpenAI clients
src/visa_agent/documents/   synthetic files and PDF evidence extraction
src/visa_agent/delivery/    deterministic review pack
src/visa_agent/storage/     SQLite, idempotency, outbox
samples/emails/             committed synthetic .eml event log
knowledge/                  versioned official-policy snapshot
tests/                      risk-oriented verification
```

## Current limitations

The offline extractor recognises deliberately marked synthetic PDF fixtures; it is not a production
OCR or fraud-detection system. Authentication, malware scanning, encrypted object storage, retention
jobs, operator roles, Gmail OAuth bootstrapping, and real-user validation remain production work.
Passing tests is internal evidence, not proof of applicant outcomes or external usability.
The committed Agent corpus and evaluator are synthetic; a live score exists only when
`eval_output/agent_eval.json` is produced with an explicitly selected provider model.
