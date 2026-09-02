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
| Three-email case progression | Automated simulation | Replays committed `.eml` fixtures and reaches the expected blocked → corrected → confirmed states | Standard MIME attachments, free-form customer language, provider delivery |
| Inbound attachments | Automated simulation | Demo-specific header points to generated local PDFs | Actual MIME extraction, malformed files, limits, filename safety, Gmail attachment payloads |
| Human-like replies | Automated simulation | Deterministic template produces one reply per fixture | Natural but bounded model drafting, tone evaluation, clarification loops |
| Gmail API boundary | Implemented only | List/get/download/send methods exist | Contract tests, message conversion, OAuth sandbox, retry/error handling, outbox consumption |
| WhatsApp | Not implemented | Explicitly outside the current slice | Provider choice, webhook verification, media handling, sandbox experiment |
| Delivery safety gate | Automated simulation | Eight deterministic checks block delivery; current gate is rechecked at download | Fault injection around stale packs, concurrent mutation, recovery evidence |
| Event idempotency | Automated simulation | Replaying the same three provider IDs does not change counts | Provider redelivery, partial failure between persistence and send, concurrent duplicates |
| Pack determinism | Automated simulation | Twenty clean runs generate the same ZIP hash | Cross-platform/runtime reproducibility and migration compatibility |
| Model extraction | Implemented only | Optional schema-bound OpenAI adapter exists | Mock/provider contract tests, evaluation corpus, variance and failure thresholds |
| Prompt-injection resistance | Narrow automated simulation | Offline fixture extractor ignores one obvious injection string | Live-model evaluation, document injection, schema escape, exfiltration and tool-authority tests |
| Human usability | Internal review only | Implementer inspected the local Demo | Independent interviewer and applicant task observation |

## High-risk gaps discovered

1. The committed fixtures reference attachments through `X-Demo-Attachments`; they do not yet prove
   that a normal MIME email with attached PDFs can enter the workflow.
2. The offline extractor reads a hidden `DEMO_FACTS` block. It proves deterministic orchestration,
   not natural-language understanding.
3. `GmailAdapter` is not connected to `WorkflowService`; no inbox poller converts a Gmail payload
   into an `InboundEvent`, and no worker consumes the persisted outbox.
4. Outbox rows have no delivery state, attempt count, next-attempt time, or permanent-failure field.
5. No ordering or sender-identity policy is enforced for later messages in an existing thread.
6. Model timeout, refusal, invalid schema, partial extraction, and inconsistent repeated outputs are
   documented but not exercised.
7. WhatsApp webhook authenticity, message ordering, media download, and reply delivery do not exist.

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

### E-03 — Agent stability evaluation

**Question:** Does the selected model produce schema-valid, evidence-grounded proposals and bounded
replies across repeated runs and adversarial cases?

**Minimum corpus:** normal intake, missing facts, contradictions, unsupported route, serious history,
prompt injection, malicious document text, multilingual mail, provider timeout, refusal, invalid
schema, and partial output.

**Report separately:** schema-valid rate, critical-field precision/recall, unsupported-claim rate,
boundary-violation rate, clarification quality, latency, and cost. A single aggregate “accuracy” score
is not sufficient.

### E-04 — Gmail sandbox

Use a dedicated test account and synthetic applicant only. Exercise OAuth, thread discovery, actual
MIME/attachment download, reply headers, provider redelivery, revoked credentials, quota/transient
errors, and permanent failures. Record message IDs and redacted timestamps, never tokens or raw PII.

### E-05 — WhatsApp sandbox

Begin only after E-01 through E-03 pass because WhatsApp must reuse the same typed event contract,
state machine, outbox semantics, and delivery gate. Validate webhook signatures, reply-window rules,
media limits, ordering, duplicate delivery, and an email handoff for the final review pack.

### E-06 — External usability

Ask a nontechnical evaluator to start the repository, explain why the first pack was withheld, find
the exact correction request, verify why the final pack was released, and download it. Observe the
task without coaching. Internal heuristic scores do not count as this evidence.

## Release rule

The GitHub submission may call itself a **credential-free deterministic Demo** after E-01 through
E-03 pass. It may claim **Gmail tested** only after E-04. It may claim **WhatsApp tested** only after
E-05. It may claim **easy for nontechnical evaluators** only after E-06 with recorded observations.
