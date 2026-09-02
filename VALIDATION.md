# Validation status and experiment plan

This document separates implemented code, automated simulation, provider-sandbox evidence, and
external user validation. A feature is not described as validated unless its evidence class is
explicitly recorded here.

## Evidence classes

| Class | Meaning |
|---|---|
| Implemented | Code exists and can be inspected. It may still be untested. |
| Automated simulation | A deterministic or mocked test ran locally/CI. No external provider was exercised. |
| Provider sandbox | A real provider account/API was exercised with synthetic data. |
| External observation | A person outside the implementation loop completed a realistic task. |

## Current capability ledger

| Capability | Current evidence | Honest status | Missing proof |
|---|---|---|---|
| Three-email case progression | Automated simulation | Replays both committed fixtures and standards-compliant MIME messages, reaching the expected blocked → corrected → confirmed states | Free-form customer language and provider delivery |
| Inbound attachments | Automated simulation | Standard MIME PDFs are extracted with filename, type, count, and size controls | Gmail attachment payloads and provider redelivery |
| Human-like replies | Automated simulation | Deterministic template produces one reply per fixture | Natural but bounded model drafting, tone evaluation, clarification loops |
| Gmail API boundary | Automated simulation | Raw MIME ingestion, threaded send, sent lookup, outbox mapping, OAuth file safety, and error classes pass fake-provider tests | Real OAuth, mailbox delivery, attachment/thread behaviour, quota and revoked-token evidence |
| WhatsApp | Automated simulation | Twilio Sandbox boundary covers signature-first text/PDF intake, channel isolation, finite send failures, idempotency, and reply-window enforcement | Durable public webhook, account/device sandbox, real media/reply/status callbacks |
| Delivery safety gate | Automated simulation | Eight deterministic checks block delivery; current gate is rechecked at download | Fault injection around stale packs, concurrent mutation, recovery evidence |
| Event idempotency | Automated simulation | Accepted and rejected provider IDs are persisted once; concurrent outbox claims are exclusive | Provider redelivery and external delivery reconciliation |
| Pack determinism | Automated simulation | Twenty clean runs generate the same ZIP hash | Cross-platform/runtime reproducibility and migration compatibility |
| Model extraction | Automated simulation | Schema-bound adapter contract, grounded patch guard, retries, abstention, and a 12-case corpus exist | Repeated provider runs, threshold calibration, and measured variance |
| Prompt-injection resistance | Automated simulation | Unknown fields, ungrounded excerpts, conflicting values, unsafe reply claims, and injected failures are contained | Repeated live-model email/document injection and exfiltration tests |
| Human usability | Internal review only | Implementer inspected the local Demo | Independent interviewer and applicant task observation |

## High-risk gaps discovered

1. The offline extractor reads a hidden `DEMO_FACTS` block. It proves deterministic orchestration,
   not natural-language understanding.
2. Gmail is connected to the shared ingestion/workflow/outbox contracts under fake-provider tests,
   but no OAuth account or real mailbox has been exercised.
3. Model timeout, malformed values, ungrounded evidence, conflicts, and unsafe reply claims are
   exercised locally, but refusal and repeated live-provider variance are not yet measured.
4. The outbox reconciliation contract is provider-neutral and locally simulated; Gmail search by
   deterministic RFC Message-ID and its consistency window are not yet verified.
5. WhatsApp has a local signature/media/send boundary, but durable webhook ingestion, ordering,
   authenticated provider media download, status callbacks, and real provider delivery remain open.

## Experiment sequence

### E-01 — Realistic local email transport

**Question:** Can standard MIME emails with real PDF attachments travel through the complete local
workflow and produce inspectable replies without demo-only attachment headers?

**Acceptance:**

- Three standards-compliant MIME messages are parsed from bytes.
- Attachments are extracted with safe filenames and explicit size limits.
- Duplicate provider IDs and duplicate attachment bytes cause no duplicate effects.
- Missing IDs, oversized attachments, malformed dates, and unsupported attachments fail safely.
- The same blocked → correction → confirmation → pack story remains observable.

Evidence class: automated simulation. This does not count as Gmail validation.

**Run 1 — 2026-09-02:** partial pass. Three multipart MIME messages carrying nine real PDF
attachments completed the expected `blocked → awaiting_confirmation → ready` flow without the
demo-only attachment header. Replaying all three provider IDs returned `duplicate_ignored` and did
not change persistent counts. Focused tests also passed safe basename handling, unsupported-file
rejection, configured attachment-size rejection, and missing-provider-ID rejection. Remaining before
E-01 is complete: malformed date/header corpus, same-name/different-content collision, corrupted PDF,
attachment-count/total-message limits, and observable safe failure instead of an uncaught parser or
document exception.

**Run 2 — 2026-09-02:** E-01 parser/workflow acceptance passed. Added invalid-date rejection,
per-file/aggregate/message-size limits, attachment-count limits, same-name/different-content
collision preservation, and corrupted/missing PDF handling. A corrupt PDF is now retained as
`NEEDS_REPLACEMENT`, opens a blocker, and produces a replacement request instead of terminating the
workflow. The suite now has 22 passing tests; twenty clean Demo runs remain byte-identical and
100/100 concurrent console reads pass. Persisting transport-level parse failures for retry or manual
inspection is intentionally tracked by E-02 rather than counted as complete channel delivery.

### E-02 — Channel and outbox fault injection

**Question:** Does the system recover predictably from duplicate, delayed, out-of-order, transient,
and permanent channel failures?

**Acceptance:** no duplicate customer reply or pack; bounded retries; permanent failures are visible;
late messages cannot silently corrupt a delivered case.

**Run 1 — 2026-09-02:** partial pass. Outbox delivery now persists `PENDING`, `SENDING`, `RETRY`,
`SENT`, and `FAILED`, including attempt count, next-attempt time, redacted error, sent time, and
provider message ID. Tests prove one-time successful dispatch, final-pack attachment, exponential
backoff, retry exhaustion, permanent failure, and transactional claiming across two workers. A row
left in `SENDING` is deliberately not auto-reclaimed because provider success is ambiguous after a
worker crash. Remaining: provider reconciliation for ambiguous sends, inbound parse-failure records,
sender/thread ownership, and delayed/out-of-order event policy.

**Run 2 — 2026-09-02:** inbound mutation guards passed. A sender whose normalized address does not
match the applicant is recorded as `THREAD_SENDER_MISMATCH` with no case mutation or reply. An event
older than the last accepted provider timestamp is held as `OUT_OF_ORDER_EVENT`. New information for
a ready/delivered case is held as `FINALIZED_CASE_NEW_EVENT` for a controlled human revision instead
of silently reopening or corrupting the pack. Replayed rejected provider IDs remain idempotent.
Failure records contain codes and minimal operational detail, not raw message bodies. Remaining:
ambiguous-send reconciliation and transport-level parse-failure persistence.

**Run 3 — 2026-09-02:** E-02 local acceptance passed. The ingestion boundary converts malformed,
oversized, and unsupported email failures into one redacted, idempotent operational record keyed by
provider message ID; raw message bodies are not stored in failure detail. An outbox row stranded in
`SENDING` can be reconciled by its deterministic RFC Message-ID: a provider match marks it `SENT`, a
transient lookup leaves it untouched for a later check, and a definite no-match moves it to
`AMBIGUOUS`. `AMBIGUOUS` rows are excluded from automatic dispatch and require an explicit operator
retry decision. Focused contract and integration tests pass. This is provider-neutral automated
simulation; Gmail-specific lookup and delivery behaviour remain E-04 evidence.

### E-03 — Agent stability evaluation

**Question:** Does the selected model produce schema-valid, evidence-grounded proposals and bounded
replies across repeated runs and adversarial cases?

**Minimum corpus:** normal intake, missing facts, contradictions, unsupported route, serious history,
prompt injection, malicious document text, multilingual mail, provider timeout, refusal, invalid
schema, and partial output.

**Report separately:** schema-valid rate, critical-field precision/recall, unsupported-claim rate,
boundary-violation rate, clarification quality, latency, and cost. A single aggregate “accuracy” score
is not sufficient.

**Run 1 — 2026-09-02:** local boundary fault injection passed; provider quality remains untested.
Every model adapter is now wrapped by a mandatory guard that retries extraction at most once, then
abstains into human review. It rejects unknown fields, source excerpts absent from the email,
low-confidence updates, invalid field values, and conflicting values before state mutation. Empty,
oversized, failing, or prohibited outcome-claim replies use deterministic non-advisory wording; a
human-review case never delegates its customer message to the model. A committed 12-case synthetic
corpus covers normal and missing intake, contradictions, unsupported routes, serious history, prompt
injection, malicious document text, Chinese email, sponsorship, negation, non-inference, and vague
correction. The live evaluator reports schema validity, field precision/recall, unsupported claims,
raw boundary violations, human-review decisions, ambiguity detection, latency, token totals, and
cost separately. Forty-two repository tests pass. No `OPENAI_API_KEY` is present, so no provider
call or live-model score is claimed. Remaining: run repeated candidates, set release thresholds, and
record refusal/partial-output behaviour plus dated cost evidence.

### E-04 — Gmail sandbox

Use a dedicated test account and synthetic applicant only. Exercise OAuth, thread discovery, actual
MIME/attachment download, reply headers, provider redelivery, revoked credentials, quota/transient
errors, and permanent failures. Record message IDs and redacted timestamps, never tokens or raw PII.

**Preparation run — 2026-09-02:** fake-provider contract passed; provider sandbox not started. Gmail
raw MIME now enters the same bounded ingestion and workflow service, and provider re-polling remains
idempotent. Outbox replies preserve the inbound RFC Message-ID and References chain, set a
deterministic outbound Message-ID, attach the ready ZIP, and search Sent by that ID for ambiguous-send
reconciliation. Tests cover unpadded base64url payloads, 429/5xx transient classification, permanent
4xx classification, missing send IDs, private token permissions, and missing-credential guidance.
The optional SDKs import successfully. No OAuth client or token is present, so no Gmail request was
made and E-04 remains open. `GMAIL_SANDBOX.md` contains the explicit test-account runbook.

### E-05 — WhatsApp sandbox

Begin only after E-01 through E-03 pass because WhatsApp must reuse the same typed event contract,
state machine, outbox semantics, and delivery gate. Validate webhook signatures, reply-window rules,
media limits, ordering, duplicate delivery, and an email handoff for the final review pack.

**Preparation run — 2026-09-02:** provider selected and local boundary tests passed; provider sandbox
not started. Twilio WhatsApp Sandbox was selected for functional Demo testing without a registered
WhatsApp Business sender. Signed form payloads become the same channel-neutral `InboundEvent` and
use MessageSid as the idempotency key. Tests cover signature-first rejection, exact sender matching,
one allow-listed Twilio-hosted PDF, non-PDF/multiple/oversized/SSRF rejection, channel worker
isolation, successful text send, missing SID, transient/permanent errors, replay, and deterministic
24-hour free-form reply expiry. The final ZIP is intentionally withheld from WhatsApp and remains a
secure Email/review-console handoff. No Twilio account, device, public webhook, or external message
was used. Remaining before sandbox: durable fast-ack queue/worker, public route, authenticated media
download, joined test device, real reply/status callbacks, and redacted evidence report.

### E-06 — External usability

Ask a nontechnical evaluator to start the repository, explain why the first pack was withheld, find
the exact correction request, verify why the final pack was released, and download it. Observe the
task without coaching. Internal heuristic scores do not count as this evidence.

## Release rule

The GitHub submission may call itself a **credential-free deterministic Demo** after E-01 through
E-03 pass. It may claim **Gmail tested** only after E-04. It may claim **WhatsApp tested** only after
E-05. It may claim **easy for nontechnical evaluators** only after E-06 with recorded observations.
