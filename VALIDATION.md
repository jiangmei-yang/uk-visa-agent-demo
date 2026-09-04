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

Updated 2026-09-04. The dated experiment entries below are historical records, not current
configuration assertions. Overall acceptance is **incomplete**. The current local suite has
350 passing tests; this is not a naturalness score or a general accuracy/reliability percentage.

| Capability | Current evidence | Honest status | Missing proof |
|---|---|---|---|
| Case progression and final ZIP | Local simulation + bounded live Gmail evidence | Fixture-document thread B reached ordinary confirmation and recipient-visible ZIP; ordinary-document thread D completed attachment/correction stages only ([evidence](GMAIL_LIVE_EVIDENCE.md)) | Ordinary-document end-to-end final delivery with an authorized participant |
| Inbound attachments | Local tests + real Gmail/OCR evidence | Approved fictional PDFs arrived through Gmail; ordinary text/scan extraction and correction were exercised; non-identity summary remains blocked ([evidence](GMAIL_LIVE_EVIDENCE.md)) | Broader document/OCR coverage, translation matching, real authorized materials |
| Human-like replies | Internal reading + real-model evaluations | Chinese multi-stage pacing improved; mechanical passes still produced formulaic replies ([review](CONVERSATION_REVIEW.md)) | Independent tone/usability observation, broader multi-turn and English review |
| Gmail service | Real authorized account + local supervised service | OAuth, threaded replies, fixture ZIP and one crash-after-acceptance reconciliation verified; registered-sender incremental worker deployed ([service](GMAIL_AUTOMATIC_SERVICE.md)) | Public onboarding, live expiry/quota/revocation recovery, always-on deployment |
| Incremental Gmail intake | Local integration + live single-message migration | Durable pagination/backlog handling, 152-message runner test and preserved live migration; two real negative probes did not prove expiry recovery ([details](GMAIL_INCREMENTAL_SYNC.md)) | Real large backlog/expired-history recovery and operational recovery for unavailable candidates |
| WhatsApp | Local signed SDK tests + tunnel smoke only | Inbound/media/outbox and signed status receipt boundaries implemented ([runbook](WHATSAPP_SANDBOX.md)) | Configured account, enrolled device, real text/PDF/reply/status exchange and final handoff |
| Delivery safety | Local tests + bounded Gmail scenarios | Source/confirmation gates, held-update delivery/download stops and exact archive hash checks ([reliability](RELIABILITY.md)) | Broader concurrent mutation/recovery and ordinary-document release evidence |
| Event/send idempotency | Local tests + specific live crash/reconciliation | Provider IDs deduplicate; accepted Gmail send recovered without resend; uncertain outcomes withheld ([evidence](GMAIL_LIVE_EVIDENCE.md)) | Other live outage windows; lost-SID Twilio recovery remains manual |
| Pack determinism | Local/CI fixture runs | Twenty identical fixture ZIPs documented; Docker state migration verified ([accuracy scope](ACCURACY.md)) | Independent cross-platform reproducibility and realistic-material output review |
| Model extraction/injection | Synthetic corpora with real DeepSeek + local guards | Separate precision/recall, negative-fact and perturbation reports; failures retained ([reports](ACCURACY.md)) | Real-user distribution, broader document attacks, monitoring and drift |
| Human-review recovery | Local integration | Held messages preserved; audited operator retry for non-finalized Gmail intake resets consent ([scope](HUMAN_REVIEW_RECOVERY.md)) | Nontechnical operator UI, authenticated roles, finalized-case revision and real recovery journey |
| Human usability | Internal browser review + owner's critical feedback | Launchers/guided flow exist; guide corrected to describe persistent state ([walkthrough](START_HERE.md)) | Uncoached independent interviewer/applicant observation; no full-mark claim |
| Local data controls | Local tests + earlier browser review | Export/deletion includes held updates and review actions; held bodies are explicitly retained pending review ([scope](HUMAN_REVIEW_RECOVERY.md)) | Retention automation, access roles, encrypted storage, provider/backup deletion policy |

## High-risk gaps discovered

1. The credential-free extractor still proves fixture orchestration, not understanding arbitrary
   applicant emails. The separate real-model/ordinary-document path must not be conflated with it.
2. Ordinary-document final ZIP acceptance needs an authorized participant and explicit local/model
   processing consent. Participation has been requested; an identity summary cannot be relabelled
   a valid passport to obtain a pass.
3. Twilio configuration is absent from the currently inspected process environment. Account/device
   setup and actual exchanges are external prerequisites, not something passing local tests supplies.
4. Real-model evaluation corpora remain bounded and synthetic. Owner feedback already disproved
   the assumption that a mechanically passing reply necessarily sounds natural.
5. Gmail rewrites RFC Message-ID; the implemented correlation-header recovery passed a specific
   real crash test. Other outages and both negative history-recovery probes remain unproven.
6. Independent usability evidence, finalized-case revision and production privacy/operations controls
   are incomplete. None is waived by a higher automated-test count.

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

**Preparation run 2 — 2026-09-03:** DeepSeek was added as an explicitly separate candidate adapter,
not as an OpenAI alias. The adapter uses DeepSeek's JSON Chat mode, omits OpenAI-only request fields,
disables thinking for the narrow extraction comparison, and returns the same strict
`CasePatch` to the existing mandatory guard. `deepseek-v4-flash` joins Luna and Terra as a candidate,
but no `DEEPSEEK_API_KEY` is present and no provider call or score is claimed.

**Provider run 3 — 2026-09-04:** `deepseek-v4-flash` passed the 15-case release corpus three times
(45/45). A separate 75-input stress run then applied realistic mail noise, quoted reply history,
English injection suffixes and Chinese injection wrappers. The first run exposed an ungrounded
multi-field proposal; the deterministic guard rejected every invented excerpt. After serializing
mail as explicit untrusted data and adding a literal excerpt self-check, the final full run reached
100% schema validity and critical precision, 97.37% critical recall, 0% unsupported claims, 0%
unsafe boundary violations, and 100% human-review and ambiguity decisions. p95 latency was 3.31
seconds; conservative peak/cache-miss cost was USD 0.046395. Omitted facts fail closed at the
completeness gate and cannot authorise a pack.

**Provider run 4 — 2026-09-04:** DeepSeek completed the full synthetic natural-language workflow
three independent times, not just isolated extraction. Nine messages triggered three identical
`blocked → awaiting_confirmation → ready` sequences; 117/117 workflow, gate, pack, and reply checks
passed, all nine model replies were accepted without fallback, and semantic repeat consistency was
100%. All three runs generated the same ZIP hash. The default guided browser lab remains
deterministic and credential-free, while this report isolates actual model behaviour.

**Internal product run — 2026-09-04:** the Docker build was clicked end-to-end in the in-app browser.
The initial lab state hid the unavailable pack, the first message showed two blockers and 7/10 gate
checks, the correction reached 9/10 without releasing the pack, and final confirmation reached 10/10
with three recorded exchanges and a visible pack action. Desktop and narrow layouts had no DOM
horizontal overflow; keyboard arrow navigation switched the case walkthrough tabs; no console errors
were recorded. This is internal review, not E-06 external observation.

**Policy maintenance — 2026-09-04:** a weekly GitHub Action now runs the same policy-window check as
the live delivery gate. Expiry produces a failing workflow and requires manual GOV.UK source review;
the job does not automatically rewrite legal rules.

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

**Live follow-up — 2026-09-04:** the preparation statement above is superseded for account presence.
Real OAuth, threaded replies, fixture attachments/ZIP, ordinary-language attachment corrections,
registered-sender automatic intake and a send-acceptance crash/reconciliation were exercised.
Details and failures are in `GMAIL_LIVE_EVIDENCE.md` and `GMAIL_INCREMENTAL_SYNC.md`.
E-04 remains **partial**, because its full failure/recovery scope and ordinary-material final
delivery are not proven. In particular, two real history-cursor probes have `passed: false`.

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
was used. A later local run added the 64 KB public form boundary, fail-closed configuration, durable
fast-ack queue, expiring worker leases, bounded retry/dead-letter states, successful-payload
redaction, authenticated size-bounded media download with redirects disabled, and explicit one-batch
inbound/outbound worker commands. Remaining before sandbox: joined test device, exact HTTPS tunnel,
real signed request/media/reply/status callbacks, evaluated live model, and redacted evidence report.

**Preparation run 2 — 2026-09-03:** a provider-only FastAPI surface and free TryCloudflare launcher
were added. The public surface contains only health and the Twilio webhook; the review console, case
API, and pack download return 404. The webhook still fails closed without test credentials. A public
URL alone is infrastructure preparation, not Twilio sandbox evidence.

**Free gateway smoke run — 2026-09-03:** the launcher created a real random TryCloudflare HTTPS URL
and reached the local provider gateway. `/health` returned 200, `/` and `/api/cases` returned 404,
and an unsigned/unconfigured WhatsApp POST returned 503. The tunnel was then stopped. This proves
the free ingress path and isolation, but no Twilio request or message was involved.

**Local follow-up — 2026-09-04:** signed delivery-status receipt ingestion and conflict reduction
are implemented and SDK-signed local tests pass. This does not supply the missing joined device,
real callbacks or actual delivery evidence. E-05 remains open.

### E-06 — External usability

Ask a nontechnical evaluator to start the repository, explain why the first pack was withheld, find
the exact correction request, verify why the final pack was released, and download it. Observe the
task without coaching. Internal heuristic scores do not count as this evidence.
Use `evals/external_usability_protocol.md` to record the uncoached run and any assistance. No
completed participant record is currently present; E-06 remains open.

## Release rule

The GitHub submission may call itself a **credential-free deterministic Demo** after E-01 through
E-03 pass. It may claim **Gmail tested** only after E-04. It may claim **WhatsApp tested** only after
E-05. It may claim **easy for nontechnical evaluators** only after E-06 with recorded observations.
