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

**Current limitations:** the credential-free guided lab is not a general-purpose document agent.
Its PDF extraction primarily uses labelled fixtures; arbitrary PDFs and scans are not yet fully
supported and unclassified PDFs are held for manual review. The real Gmail trial now accepts
ordinary, unmarked enquiries, follows Chinese/English, asks a few questions at a time, and supports
context-bound natural confirmation. A supervised service automatically replies to one registered
test sender; final-pack dispatch still requires explicit reviewed sending. This is not public
intake or unattended production. See [service boundaries](GMAIL_AUTOMATIC_SERVICE.md) and
[live successes, failures and remaining work](GMAIL_LIVE_EVIDENCE.md).

The latest [adviser reply experiment](NEXT_STEP_ADVICE.md) combines sourced answers with one
case-aware next step, instead of another full questionnaire. Its first new holdout is **6/8**;
the original failures and post-holdout local repairs are published. See [current acceptance
gaps](VALIDATION.md), not just the automated-test count.

The [latest reliability work](GMAIL_RECOVERY.md) adds recoverable sent-evidence lookup, safer
credential replacement and verified lab downloads. A real isolated token-refresh/rejection probe
passed without sending mail or changing the saved credentials; it is not live revocation recovery.

The live Gmail runner additionally supports bounded ordinary-PDF text extraction and local OCR.
A real two-turn Gmail experiment received four fictional ordinary PDFs, answered a sourced
booking question and applied a date correction without re-upload. The identity summary stayed
withheld: this is not yet a complete ordinary-document-to-final-pack acceptance test.

## Easiest path for an interviewer

No Python, API key, Gmail account, or terminal knowledge is required. Install and open
[Docker Desktop](https://www.docker.com/products/docker-desktop/), then:

- macOS: double-click `START_DEMO.command` (and approve opening it if macOS asks).
- Windows: double-click `START_DEMO_WINDOWS.bat`.

The launcher builds the app, prepares the complete synthetic case, waits until the health check
passes, and opens <http://127.0.0.1:8000>. Use the matching `STOP_DEMO` file when finished. See
[START_HERE.md](START_HERE.md) for the one-page walkthrough and troubleshooting.

From the finished case, choose **Try the workflow** to run the three-message journey yourself.
This guided lab uses the actual workflow, evidence ledger, delivery gate, and pack generator. It is
synthetic and credential-free; the page states clearly that its extractor is deterministic rather
than an external model.

The first launch normally takes a few minutes because Docker downloads the base image. Later
launches reuse it. The console's synthetic data is kept in the local Docker named volume
`review-runtime`; restarting or rebuilding no longer resets existing cases. Do not run
`docker compose down -v` unless you intend to delete that data. Gmail trial state remains
separate, under the private local `data/` directory.

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
Email / WhatsApp provider boundary
        │
        ▼
typed, channel-neutral InboundEvent ── idempotency key
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
make accuracy    # committed workflow-accuracy and safety scorecard
make lint        # Ruff
make typecheck   # strict mypy
make stability   # repeated clean runs + concurrent review-console reads
make policy-check # fail when the committed policy review window has expired
MODEL=<model-id> make agent-eval-live  # optional OpenAI provider evaluation
MODEL=deepseek-v4-flash make agent-eval-deepseek  # optional DeepSeek evaluation
make agent-eval-stress # optional 75-input DeepSeek formatting/injection stress run
make workflow-eval-deepseek # optional complete DeepSeek conversation-to-pack run
make web         # local case-review console
make webhook     # provider-only local webhook gateway on port 8001
make start       # build and start the complete Docker demo
make stop        # stop the Docker demo
make clean       # delete disposable local demo data
```

A weekly GitHub Actions check runs `make policy-check`. After the policy review boundary it fails
closed and requires a human to recheck the linked GOV.UK sources before extending the snapshot.

## Optional live providers

Install optional provider SDKs with `uv sync --extra dev --extra live`.

For local DeepSeek use, place the key in `.secrets/deepseek_api_key.txt` and set file permissions to
`0600`. The directory is excluded from Git and Docker images. `DEEPSEEK_API_KEY` and
`DEEPSEEK_API_KEY_FILE` remain supported for managed environments.

- `OpenAIStructuredLLM` uses a Pydantic schema through the Responses API. The model ID must be chosen
  explicitly after evaluation. It returns proposals only; the workflow grounds, validates, and
  applies them behind a mandatory bounded guard.
- `DeepSeekStructuredLLM` is a separate adapter for DeepSeek's OpenAI-compatible JSON Chat API. It
  embeds the same JSON schema, validates the returned object locally, disables thinking for bounded
  extraction, and does not send OpenAI-only request fields. Compatibility is not
  treated as equivalent behaviour: `deepseek-v4-flash` must pass the same repeated corpus and guard
  thresholds before it can be selected.
- `GmailAdapter` accepts an OAuth-authenticated Gmail API service, polls a configured query, preserves
  thread headers, downloads raw MIME/attachments, and can send replies/ZIP attachments through the
  transactional outbox. Follow [GMAIL_SANDBOX.md](GMAIL_SANDBOX.md) for the dedicated synthetic test
  account. Never commit OAuth credentials or tokens.
- `TwilioWhatsAppWebhook` and `TwilioWhatsAppSender` provide the optional WhatsApp Sandbox boundary.
  They reuse the same typed event, durable inbound queue, case workflow, and channel-isolated outbox.
  Provider commands process one explicit batch at a time, so no external message is sent merely by
  launching the credential-free Demo. Follow
  [WHATSAPP_SANDBOX.md](WHATSAPP_SANDBOX.md); local contracts are not real-provider evidence.

`START_FREE_WEBHOOK_TUNNEL.command` starts a free TryCloudflare HTTPS URL backed by a separate
provider-only app. It exposes `/health` and the Twilio webhook—not the review console, case API, or
pack download. The webhook remains fail-closed until Twilio test credentials are supplied.

Live mode is intentionally not part of the default assessment path. Email and WhatsApp sandbox
claims remain withheld until their separate provider experiments pass.

The local review app binds to `127.0.0.1` in Docker. A case can be exported as JSON from the review
page. Deletion requires a browser confirmation and an exact case-ID request header, then removes the
case records and case-owned generated artifacts. Processed raw inbound messages are not retained.
Exception: applicant updates paused for human review, controlled revision or out-of-order handling
retain their event body and attachment references privately in the case database. They are included
in the case data export and removed from the database on case deletion. This does not imply remote
mailbox deletion or removal of independently retained uploads/backups. See `HUMAN_REVIEW_RECOVERY.md`.

## Policy sources

The snapshot was checked on 2026-09-02 and reverified against the same live GOV.UK guidance on
2026-09-03. It is versioned as 2026-02-25, matching GOV.UK's current update date. Rules contain
source metadata and a review deadline. The
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
src/visa_agent/llm/         deterministic, OpenAI, and DeepSeek clients
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
The committed Agent corpus and evaluator are synthetic. DeepSeek reports cover extraction stress
and one complete natural-language conversation-to-pack path; they are not real-applicant evidence.
See [ACCURACY.md](ACCURACY.md) for the separate workflow and live-model scorecards.
