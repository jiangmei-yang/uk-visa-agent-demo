# Current-home residence duration: staged implementation

Status: **incomplete, unreleased**. GOV.UK application information checked
2026-09-07 explicitly includes the current home address and how long the applicant
has lived there: [official application information](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa).
This does not justify deriving an exact move-in date from an approximate duration.

## 2026-09-07 required gate and sent-question integration

The duration is now in `BASE_REQUIRED_FACTS`: all cases, including legacy cases,
need it before final readiness; existing provenance and confirmation checks apply.
EN/ZH missing-question text is ordered after the home address. The synthetic first
demo email visibly states `I have lived at my current address for two years.` and
the offline adapter quotes that statement, not an invented hidden default.
The actual guided web lab still passes its staged blockers/confirmation/download
tests with the updated fixture.

Short duration answers require a single matching actually SENT question in the
same case/recipient/thread, the current last question and unchanged current-home
binding. The model only proposes the literal value; it cannot assert that a
question was sent. A bounded short uncertainty response records its source event,
excerpt, question event and address, stays missing at the gate and suppresses
repeat asking. Changing the address clears stale question/deferral context while
retaining deferral history. Unsigned question hints, unsent questions and old-home
context cannot ground short answers.

Five captured-workflow tests cover supplied duration, retention and address
changes; actually sent short answers; persistent uncertainty; unsent question
and different-address rejection. With the explicit pre-populated next-step fixture
updated, the duration/next-step/family group passed **29 tests** (1.11s). The duration
question tests explicitly clear that fixture's duration and collect it through
the actual inbound turn; no production case receives a default value.

**Release remains unfinished.** Full-regression fixture migration, broader short
uncertainty and duration wording, friendly output labels/rendering, and a fresh
real-model/clean-container run still need evidence. The historical foundation
notes below describe the preceding stage, not a current exemption from the gate.

## 2026-09-07 readable output and visual QA

The English customer-facing field label is now “Time living at your current home”;
the PDF profile row uses “Time living at current home” directly below the address.
Supplied English/Chinese duration text keeps its original precision. An active
deferral for that same address renders as deferred/not yet supplied, while old
uncertainty from a different address cannot replace the new home's missing row.
No source-event or question IDs are printed in this readable section.

`scripts/residence_duration_preview.py` generates a one-page synthetic preview
through the production profile-row and PDF renderer. The PDF skill's visual
inspection of `output/pdf/residence-duration-v1/summary.pdf` verified the supplied,
Chinese, deferred and changed-address cases: readable glyphs, no observed overlap
or clipping, normal footer/page number. SHA-256:
`6be130019f4c2d4f518c3ab4d9b8377dbcc1a77eaf48fd6094dc12367d57293e`.
It is explicitly rendering QA, not a completed application or actual release.

The real synthetic demo ZIP test now requires the same `two years` in the PDF
summary and JSON profile, exactly one active duration fact, and its exact visible
first-email source sentence. The widened output/workflow/demo/web subset passed
**24 tests** (2.53s); Ruff and strict Mypy (92 modules) passed. No full regression,
live model call, mailbox message or deployment was performed in this output step.
Full-pack visual QA, multilingual customer summary review and fresh source-bound
live/container acceptance remain necessary.

## 2026-09-07 real-model correction/move experiment

The four-turn `residence` scenario in `consultant_journey_probe.py` covers an
initial approximate duration, explicit “three years, not two” correction, a new
home without a new duration, then a Chinese duration while trip dates remain
undecided. It uses real DeepSeek extraction, reopened isolated SQLite and captured
SENT transport; no mailbox or real documents. These are exposed development
scenarios using the probe's fixed clock, not independent usability evaluation.

`eval_output/residence_duration_2026-09-07-v1.json` retains the failed attempt:
three calls / **15,949 tokens**. Turn 2's correctly extracted new duration was
discarded because the local guard treated trailing rejection of the old amount as
negation of the entire sentence. Turn 3 stored the new address but entered a human
hold when the model called missing new-home duration an ambiguity; the old checks
incorrectly marked that turn passing. Turn 4 made no model call and the probe
recorded `StopIteration` because the held flow had no normal reply row.

The guard now distinguishes a bounded trailing old-amount contrast from an
affirmative duration, while still rejecting selection of the negated amount,
questions, negative assertions and hypotheticals. The prompt explains that an
omitted new-home duration is ordinary intake, not inherently an ambiguity. No
model uncertainty or human-review flag is forcibly cleared by this change.
The probe now checks for unexpected HUMAN_REVIEW_REQUIRED state on every turn.

`eval_output/residence_duration_2026-09-07-v2.json` then passed **4/4**, four calls /
**21,496 tokens**. Manual reply reading still found an overlong initial response
with repeated booking cautions, and no explicit receipt for the final supplied
duration. The receipt omission is now fixed; the long first response remains an
open naturalness defect, not a successful naturalness score.

The live v2 report predates the receipt edit; the explicit offline replay
`residence_duration_2026-09-07-v2-replay-receipt.json` passed **4/4** on the edited
code, with no new model calls. A permanent saved-real-proposal integration test
checks case-local source events, updated/retired evidence, correct duration in
each applicable reply, no unintended hold/delivery and retained date uncertainty.
The final duration/proposal/workflow group passed **26 tests** (0.49s). Earlier
consultant/record replay coverage passed 46 tests with one stale report-binding
check deselected (1.97s), before the final receipt change. Ruff passes; strict
Mypy passed for 92 modules before the receipt edit. No full-suite run, deployment
or actual Gmail recipient acceptance is claimed for this experiment.

## Implemented foundation

`CaseProfile.current_address_duration` preserves the supplied text with a bounded
length, separately from the address. The model prompt lists the field and forbids
inferred move-in dates, translations of the duration, country-residence periods
and relatives' residence periods. A deterministic guard requires a literal value
linked to the applicant's current home in the enclosing current sentence, with
bounded EN/ZH duration syntax. Quotes, questions, hypothetical/negated/uncertain
statements and durations attached to another person's clause do not become facts.

Accepted updates use the existing event-linked evidence history and SQLite case
storage. A changed home address retires the previous duration and its active
evidence unless that same message supplies a newly grounded duration. This avoids
silently attaching time at the previous home to the new address. A later unrelated
message retains the value. This is not postal verification or proof of residence.

Focused evidence: **15 tests passed** (0.30s), comprising 14 literal/role/precision
checks and an actual reopened-workflow synthetic conversation that supplies the
address and duration, retains it across a reply, then changes the address and
checks the old duration is superseded. Ruff and strict Mypy passed (92 modules).
No real provider, customer, Gmail send or runtime restart was used.

The first full development regression had **9 failures / 4,929 passes / 2
deselections**, 99.35s, one existing Starlette warning
(`/tmp/visa-residence-duration-foundation.log`). All nine failures were strict
historical-profile comparisons missing the new `current_address_duration: null`
key. The replay expectations now explicitly require that empty field, while
leaving old reports and all their original facts untouched. The affected two
integration files plus the new tests then passed **72 tests** (1.62s). This is
not a newly completed full-suite run; the two old source-bound reports remain
excluded and stale.

## Required before calling this intake complete

- Broaden the bounded EN/ZH short-answer and uncertainty vocabulary and test
  mixed answers/corrections; current supplied duration precision is preserved.
- Complete and verify the migrated fictional fixtures under full regression,
  then validate the clean container demo again. Real/legacy cases without the
  duration remain incomplete; no production migration supplies an invented value.
- Extend the focused visual QA above to complete generated packs and customer
  confirmation messages, including long/multilingual profiles.
- Test simultaneous address/duration corrections, broad real-model phrasing and
  address identity/formatting cases; refresh source-bound live reports.

The first gate-enabled full run had **72 failures / 4,868 passes / 2 deselections**,
99.25s (`/tmp/visa-residence-duration-gate.log`). Most were explicitly completed-
intake synthetic fixtures that lacked the newly required field. Those presets now
state a duration explicitly. The address-dialogue test instead adds a real
fictional inbound sentence and typed proposal before expecting confirmation;
the missing-duration tests continue to omit it. The production fixture quotes a
visible email sentence. No autouse fixture, real-case backfill or relaxed gate
was introduced. The first migrated subset had 421 passes / 5 failures; fixes for
the remaining dialogue fixtures and probe verifier then passed 89 tests (2.95s).

The next-step evaluator now wraps its shared replay verifier with the same
private fixture hooks used by generation/replay. It still verifies exact input
events and seed profiles and rejects reports made with different seeds; existing
historical reports are not edited. The original preparation-control probe remains
unchanged. Fresh next-step source-bound reports are required for the changed seed.

Final gate-enabled development regression: **4,942 passed / 2 deselected**,
99.20s, one existing Starlette warning (`/tmp/visa-residence-duration-gate-v2.log`).
Ruff and strict Mypy (92 modules) passed after the final changes. The exclusions
are the two older source-bound consultant/financial provider reports; those are
still stale and were not relabelled as current. No live provider calls, Gmail
messages, operator approvals or deployment occurred in this integration.

At the foundation checkpoint the field was supplied-only and not a release
requirement. That gap is now addressed by the integration above; the remaining
items and regression migration must be completed before release. The overall
delivery goal stays open.
