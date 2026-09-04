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

Updated 2026-09-05. The dated experiment entries below are historical records, not current
configuration assertions. Overall acceptance is **incomplete**.
The local suite has **3,986 passing tests**; see the latest [financial-document evidence repair](docs/FINANCIAL_DOCUMENT_EVIDENCE.md),
[application-information priority repair](docs/APPLICATION_INFORMATION_PRIORITY.md),
[school-record and undecided-date repair](docs/SCHOOL_RECORD_GUIDANCE.md),
[contextual adviser reply repair](docs/CONSULTANT_CONTEXT_REPAIR_2026-09-05.md),
[separate-email consultation repair](docs/BATCHED_CONSULTATION.md),
[mixed permission and consultation repair](docs/MIXED_PROCESSING_CONSENT.md),
[consultation continuation and obstacle repair](docs/ADVICE_CONTINUATION.md),
[independent usefulness review and repairs](docs/ADVISER_USEFULNESS_REVIEW_2026-09-04.md),
[processing-permission boundary](docs/PROCESSING_CONSENT.md),
[earlier consultant-usefulness repair](docs/CONSULTANT_REPLY_DESIGN.md),
[remaining acceptance audit](docs/END_TO_END_ACCEPTANCE_AUDIT.md),
[adviser-pacing repair](docs/ADVISER_PACING.md),
[cold-start conversation work](docs/COLD_START_CONVERSATIONS.md),
[pack/intake recovery work](PACK_RECOVERY.md),
[credential recovery](GMAIL_RECOVERY.md)
and [next-step experiment](NEXT_STEP_ADVICE.md).
Automated passes are not a naturalness score or a general accuracy/reliability percentage.

Latest ordinary financial-document repair: **3,986 passed in 60.95s**, Ruff and
strict Mypy passed (72 source files); the existing test-client warning remains.
The frozen financial/provider-replay/adapter set passed **157 checks in 0.70s**.
Financial subjects, amounts, original currencies, dates, period/basis and optional
account references now have separate page/excerpt provenance. Only like-for-like
values are compared; holder mismatches and comparable contradictions block the
gate without conversion, summing, sufficiency scoring or profile overwrites.
Ordinary financial documents lacking valid observations cannot satisfy a requirement,
and occupation/funding evidence must match the current branch. Nineteen real-model
calls over four fictional PDFs retained all v1/v2 failures and the later repairs.
The fully bound v6 run was 4/4; total provider-reported use was 38,965 tokens.
This is exposed development evidence, not real documents, Gmail delivery, a blind
holdout or an accuracy score. See [failure history and limits](docs/FINANCIAL_DOCUMENT_EVIDENCE.md)
and [rollout evidence](eval_output/financial_document_rollout_2026-09-05.json).
The Gmail worker reloaded as PID 14344 and was observed idle at 2026-09-04
18:28:26 UTC. Existing business data and unknown permission are unchanged. Docker
is healthy, all 72 source hashes match, the persistent volume is preserved and
both existing ZIP hashes are unchanged.

Latest application-information priority repair: **3,854 passed in 60.73s**, Ruff
and strict Mypy passed (70 source files); the existing test-client warning remains.
Five focused suites passed **167 checks in 1.64s**. Current requests for a visitor
application entry and steps receive the reviewed official link, Apply now,
online/save/appointment guidance without unrequested personal questions. Mixed
facts remain processable; quotes, conditions, other routes, long-validity fees,
source expiry, pause and permission boundaries retain their independent checks.
Three fictional **real DeepSeek calls**, one per case without retries, passed the
bounded content/state checks through the actual reviewed sender with a captured
transport; total provider-reported usage was **10,547 tokens**. This is not live
Gmail, a blind holdout or a naturalness/accuracy score. The original failures and
all raw synthetic observations are retained in the [repair note](docs/APPLICATION_INFORMATION_PRIORITY.md)
and [provider report](eval_output/application_information_deepseek_2026-09-05.json).
The worker reloaded as PID 9943 and was observed idle at 2026-09-04 17:51:10 UTC;
configuration, existing case/SENT projection and unknown applicant permission are
unchanged. Docker is healthy, all 70 source hashes match, the persistent volume is
preserved and both ZIP hashes are unchanged. See [rollout evidence](eval_output/application_information_rollout_2026-09-05.json).

Previous school-record and undecided-date repair: **3,703 passed in 58.19s**, Ruff and
strict Mypy passed (70 source files). Three new suites passed **171 checks in 2.41s**.
School record difficulties now get bounded existing-record checks, not the same
unavailable-letter request or an identity question. Complete actual SENT discussion
can support a short follow-up; current affirmative resolution or lost record access
retires that guidance without changing evidence acceptance. Source expiry, pause,
mixed facts/files, nearby classifier proposals, quote/negation/ownership and
independent-question scope are covered. Date-specific next-action paraphrases
and current no-link scope were also repaired. These are synthetic proposals and
captured transports, not new paid-model or independent applicant evidence. See
[failure history and limits](docs/SCHOOL_RECORD_GUIDANCE.md).
The Gmail worker reloaded as PID 7822 and was observed idle at 2026-09-04
17:32:24 UTC; configuration, existing case/SENT business projection and unknown
applicant permission are unchanged. Docker is healthy with all 70 source hashes
matching and both existing ZIP hashes unchanged. See [rollout evidence](eval_output/school_record_rollout_2026-09-05.json).
A separate read-only application-complaint probe then had 5 failures out of 8
synthetic intent combinations. These were not closed by that school-record repair;
the subsequent bounded application-information repair above addresses them.

Previous contextual adviser reply repair: **3,532 passed in 54.07s**, Ruff and strict
Mypy passed (70 source files). The four new suites passed **59 checks in 1.15s**.
Supplied location/identity now receives a contextual acknowledgement; applicable
residence evidence gets a bounded preparation action. A no-link preference does
not remove useful guidance. An explicit next-document request selects an existing
preparable requirement before an unrelated missing identity field. Source-link
formatting, quoted/conditional preferences, actual SENT, pause/review and unchanged
facts/evidence/confirmation boundaries are covered. This is captured-transport
testing with synthetic extraction, not new live-model or recipient evidence.
The Gmail worker reloaded as PID 5136 and was observed idle at 2026-09-04
17:10:57 UTC. Its settings, business projection and nine SENT records remain
unchanged; applicant permission remains unknown. Docker is healthy, all 70 Python
source hashes match, and persistent and isolated fixture ZIPs are unchanged.
See [failures and remaining observed gaps](docs/CONSULTANT_CONTEXT_REPAIR_2026-09-05.md)
and [rollout evidence](eval_output/consultant_context_rollout_2026-09-05.json).

Previous separate-email consultation repair: **3,473 passed in 53.24s**, Ruff and
strict Mypy passed (70 source files). The existing one test-client deprecation
warning remains. Five focused suites passed **184 checks in 3.03s**.
Actual unanswered applicant questions
now survive superseded drafts and restarts, and can be combined into the newest
reply without waiting for an earlier SENT omission notice. Actual complete SENT
answers consume requests; uncertain sends do not authorize copying their answers.
Overflow notices bind the correct reply event. Current no-link/topic preferences,
cancellations, source expiry and original request qualifiers are retained; old
bodies never re-enter fact extraction. See [behavior, failures and limits](docs/BATCHED_CONSULTATION.md).
This was captured-transport evaluation, not new paid-model or live mailbox testing.
The Gmail worker reloaded as PID 3444 and was observed idle at 2026-09-04
16:55:43 UTC (2026-09-05 locally). Its configuration and business projection,
including nine SENT messages, are unchanged; applicant processing consent remains
unknown. Docker is healthy, all 70 deployed Python source hashes match locally,
and both the persistent ZIP and network-disabled fixture ZIP remain unchanged.
See [dated rollout observations](eval_output/batched_consultation_rollout_2026-09-05.json).

Previous mixed-message repair: **3,348 passed in 50.54s**, Ruff and strict Mypy passed
(68 source files); one existing FastAPI/Starlette deprecation warning remains.
The three new suites also passed independently: **134 passed in 2.35s**.
Independent questions, facts and attachments in a
valid processing-grant email now continue from its original source ID. A captured
reviewed sender actually sends the FAQ answer and official link. Current
permission, source hashes, all-controls-first scanning and separate
resume/confirmation authority remain enforced. Supported operator retries survive
withdrawal/re-grant without rewriting the original envelope; scope upgrades can
request a new notice without nagging prior refusals. Obsolete unattempted control
drafts no longer consume the current reply's send slot. See [the exact boundaries,
red/green evidence and remaining limitations](docs/MIXED_PROCESSING_CONSENT.md).
No paid model call, real mailbox test send or independent applicant trial was
performed in this repair. The prior intermediate failures remain documented.
The supervised Gmail worker reloaded as PID 992 and was observed idle at
2026-09-04 16:36:00 UTC (2026-09-05 locally). Its configuration and existing
business projection, including nine SENT rows, are unchanged; permission is
still unknown. The Docker service is healthy, all 68 deployed Python source hashes
and the Gmail script match locally, the persistent ZIP is unchanged and the
isolated network-disabled demo completes. See [rollout evidence](eval_output/mixed_consent_rollout_2026-09-05.json).

Previous consultation repair: **3,214 passed in 48.15s**, Ruff and strict Mypy passed (68 source files).
The existing one FastAPI/Starlette test-client deprecation warning remains.
New consultation memory requires actually SENT omission/answer text; failed,
pending and uncertain drafts do not consume a question. Pure information
continuation bypasses extraction, while mixed facts/files retain normal processing
and can answer the remaining consultation. Current independent FAQs take priority.
Employer-letter and undecided-date obstacles give a concrete, conditional next
action without changing evidence requirements, dates or confirmations. Missing
background still prompts a necessary question rather than a false route rejection.
Retention/export notes now disclose original unfinished-question context, including
personal details and no automatic TTL. Captured actual reviewed/guarded senders and
isolated DBs were used; no paid model calls or real mailbox sends were made.
The original six full-suite pacing failures and focused continuation/mixed/casing
failures are recorded in [the repair note](docs/ADVICE_CONTINUATION.md), not hidden.
The supervised Gmail worker was reloaded as PID 97795 and observed idle at
2026-09-04 16:12:25 UTC (2026-09-05 locally), with its configuration, existing
business projection and nine SENT records unchanged. Consent remains unknown.
The persistent Docker service is healthy; all 68 deployed source hashes match,
the existing ZIP bytes are unchanged, and the separate `--network none` fixture
still completes. See [dated rollout evidence](eval_output/advice_continuation_rollout_2026-09-05.json).

Previous 2026-09-04 local repair: **3,059 passed**, Ruff and strict Mypy passed (66 source files).
The full run retains one FastAPI/Starlette test-client deprecation warning.
No paid model calls or real mailbox test sends were made in this repair. Five
[captured reviewed replies](eval_output/adviser_usefulness_repair_2026-09-04.json)
show specific document instructions, exploratory consultation without a personal
questionnaire, and foreign self-employment distinguished from work in the UK.
Original seven-item review failures remain visible; that earlier repair alone did
not close deferred FAQ continuation, evidence that cannot be obtained or
date-undecided next-step usefulness. Source documentation was rechecked on GOV.UK on 2026-09-04;
this does not add live web retrieval to the product.

Applicant-processing permission now uses a versioned canonical scope/notice,
actually SENT notice reference, explicit applicant grant and independent withdrawal
epoch. Gmail preview does not materialize attachments or call models before a grant;
all discovered controls precede normal work. Recovery uses original message IDs.
Model/document/review/send/pack/download paths check current authority. Generic
inbound defer now retains its original queue payload instead of clearing it as
processed. These are captured-transport tests, not a real applicant consent trial
or legal-compliance certification. Mixed consent-plus-business mails now have
the bounded coverage above; WhatsApp pre-download permission remains unverified.

Retained local failures during this change: first full suite **2 failed / 2,936
passed** because previous live-reader fixtures lacked the now-required consent;
these now obtain actual captured-SENT grants. Second full suite **3 failed / 3,033
passed** exposed the generic defer/replay data-loss defect (two tests written before
its implementation) and an old unsupported-topic expectation for now-supported
employment-letter instructions. That expectation was updated without weakening
the independent bank-question isolation assertion. Earlier constructor-count
expectations were updated because idle/reconciliation cycles now initialize no
model client. None of these runs is represented as a live-provider result.

The rebuilt image also completed its synthetic workflow under `--network none`:
1 case, 3 processed events, 3 outbox entries, 1 ZIP; deterministic fixture ZIP hash
`7e27fe057a2643dd892bd41e94ed47245a225ff716bb99a7a4802bb0447b641f`.

The supervised Gmail worker was reloaded as PID 95365 and completed an idle cycle
at 15:53:02 UTC; liveness was checked separately. Its configuration and existing
profile/documents/evidence/outbox projection remained unchanged (one case, nine
SENT rows). Consent migrated to **unknown**, not granted; no old mail was replayed
and no notice was fabricated at restart. The persistent Docker UI is healthy, with
the same existing ZIP bytes and named volume. See [rollout observations](eval_output/adviser_processing_rollout_2026-09-04.json).

Latest consultant experiment: three seven-case fictional DeepSeek runs exercised the actual
reviewed automatic Gmail sender with captured transport, not real mailbox sends. Original
checks printed **4/7**, **6/7**, then **7/7**; independent reading within the implementation
loop caught missing second answers and sponsor-role defects outside those checks. All three
reports remain unchanged. Seven saved-proposal replays verify follow-up repairs without
making a fourth provider run. These are exposed development cases, not blind holdouts.
Replies now offer conditional official application links and context-specific preparation
actions before one main intake question. SENT guidance memory, deferred dates and known
self-funding are retained. Sponsor-role guards reject host-only inference while preserving
explicit parents support; unknown roles remain incomplete rather than becoming evidence.

The default sender still composes reviewed content: this does not make arbitrary model
drafts safe or prove human-like naturalness. A Gmail exact profile-confirmation phrase now
needs the current unchanged profile summary actually SENT; document rereading cannot
restore confirmation by shortcut. Bounded local PDF recovery, retained Gmail metadata-404
observations and signed WhatsApp account/recipient binding are tested, not new live-channel
acceptance. Ordinary-document processing consent, broader cross-source evidence and
independent usability remain open in the [audit](docs/END_TO_END_ACCEPTANCE_AUDIT.md);
the earlier statement about absent processing-consent enforcement is superseded
by the implementation and bounded evidence above, not by a claim of real consent.
The supervised Gmail worker and persistent Docker UI loaded the changes with existing
case/outbox/ZIP projections unchanged; see [the rollout record](eval_output/consultant_value_rollout_2026-09-04.json).
Normal sync polling revisions advanced; candidate dispositions, scope and recovery actions
were preserved. The new metadata-observation table is empty, not a live 404-recovery result.

Earlier exposed-defect repair: a current FAQ plus a supplied fact no longer appends an
intake questionnaire, while explicit next steps and plain fact replies still advance.
Only actually SENT, unanswered questions provide short-answer context; quiet resumes
cannot record hidden questions. Country/city-only residence remains background evidence
but no longer completes the address gate. Incomplete address corrections replace the old
value and invalidate confirmation; replies explain the residential detail still needed.
These are isolated synthetic sender/dispatcher tests, not new model or Gmail-network
experiments. Original provider failures remain unchanged. Gmail and persistent Docker
were reloaded with unchanged prior case/outbox/ZIP projections; one transient bootstrap
error and its successful retry are retained in [the rollout record](eval_output/adviser_pacing_rollout_2026-09-04.json).

Earlier adviser experiment: new fictional six-email journeys begin with empty profiles and
reconstruct the runtime/SQLite connections each turn. Real DeepSeek extraction feeds actual
reviewed automatic reply capture, not an unused model draft. First development: **4/12 composite,
7/12 captured-SENT flow**, with an English month-name birthday rejected by local normalization
and its next five emails held. First untouched holdout after the initial repair: **6/12 composite,
12/12 captured-SENT flow**, with ordinary material overview/purpose questions still missed.
Both original reports and lexical false alarms are retained; manual reading also caught an
unsolicited checklist that passed the proxies. Subsequent exposed regressions fix broader general
overview wording, avoid private questionnaires after general overviews, preserve independent
next-step requests, explain individual-document purposes and balance-record comparisons, and
prevent translation-detail questions triggering a full checklist. Full English conversation
replay is not claimed when the original run never made those provider calls.
See [protocol, full reading and limits](docs/COLD_START_CONVERSATIONS.md).
No real Gmail sends, new ordinary-document delivery, independent user acceptance, or overall
naturalness/accuracy percentage were produced by this experiment.
The supervised Gmail worker and persistent Docker UI have loaded the repairs; existing
case/outbox and ZIP projections were unchanged. See [rollout evidence](eval_output/cold_start_rollout_2026-09-04.json).

Latest reliability work: isolated staging and accepted-source SHA checks prevent changed or stale
support files entering a retry; registered history is never silently regenerated. Failed pack work
does not acknowledge the email or starve newer unread pauses/corrections. Final replies are tied to
the current revision's latest final-confirmation event and cannot duplicate another final send in
that revision. Broken-page/rescan/restart and process-exit windows passed offline fixture tests.
The supervised Gmail worker and persistent Docker UI have loaded these changes with unchanged
existing case/reply/ZIP projections; see [the rollout record](eval_output/pack_recovery_rollout_2026-09-04.json).
This is not a new recipient-side or ordinary-document final-delivery result.

Earlier reliability work: private atomic credential replacement, mailbox-checked explicit
reauthorization, visible query-only recovery after access failure, and guided-lab archive
integrity passed isolated regressions. A real three-stage **isolated** refresh/profile probe also
passed with zero sends and original credentials unchanged. Forced local expiry and an invalid
copied refresh token do not prove natural expiry, actual revocation or live-worker recovery.
Gmail and the persistent Docker UI have been updated; prior case/reply/ZIP invariants were retained.

Earlier adviser experiment: case-aware `next_step` answers accompany independent FAQs, choose one relevant item,
and preserve date deferrals and customer pacing. The first new holdout is **6/8**, with a missed
resume expression and wrongly applying a sibling's next-step question to the applicant's case.
Original reports are retained. Post-holdout repairs are local exposed regressions, not new holdout
successes. The previous saved-output development replay remains 21/24 guard and 23/24 workflow,
including documented label ambiguity and one different-UK-route classification error. The later
reply-scope repair separates general information from personal upload requests and removes the
unsolicited tutorial from an explicitly quiet resume. Its 28 local regressions pass. The retained
new development replay is **21/24 guard, 21/24 workflow, 19/24 combined**: two additional checks
still require the old personal labels in general-reference lists; their FAIL results are not hidden
or rescored. See [the reading and limitations](NEXT_STEP_ADVICE.md#reply-scope-repair-and-retained-replay).
No new model calls or naturalness score were produced. Official UI navigation was observed only
through the mainland-China example's location-confirmation page, not account creation/submission.

Earlier: [persisted preparation pause/restart](PREPARATION_CONTROL.md) separates customer pacing
from human review and consent, blocks obsolete queued replies and retains independent facts/FAQs.
The first development run found two application-guard errors (raw 24/24, guarded 22/24); the
zero-call saved-output replay after repairs is 24/24. The first eight-case holdout is **7/8 intent,
8/8 state safety**. A mixed “continue with the next document + booking FAQ” request still loses
the next-step request. The new experiment above addresses that class separately; neither the
green local suite nor state safety retroactively changes this earlier failure.

The latest [three-arm question-understanding experiment](QUESTION_UNDERSTANDING_EXPERIMENT.md)
selects a corrected single-call input wrapper, not multi-agent orchestration. Its neutral arm
matched 24/24 new development, 24/24 exposed regression and 12/12 first-holdout topic sets.
Reply reading still found missing subanswers and unsolicited guidance. Post-measurement content
fixes passed two 24-case deterministic development replays; the failed first replays are retained.
These are separate evidence types, not an overall customer-satisfaction or accuracy percentage.

Current owner-directed priority: improve the shared agent and use Gmail as the primary realistic
test channel. Preserve the existing WhatsApp adapter contracts; defer further channel expansion
and device trials. This sequencing does not turn untested WhatsApp behavior into completed acceptance.

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
| Human-review recovery | Local integration | Held messages preserved; audited intake retry and operator-authorized finalized-case revisions preserve old archives and require fresh confirmation ([scope](HUMAN_REVIEW_RECOVERY.md)) | Nontechnical operator UI, authenticated roles, real recovery journey and recipient-side revised-pack redelivery |
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
6. Independent usability evidence, real revised-pack redelivery and production privacy/operations controls
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
