# Personal sponsor address: supplied-fact foundation

**Current status:** the later integration checkpoint at the end now adds
conditional intake, transport-bound short answers and uncertainty memory. The
foundation description below is historical, not the latest gate behavior.

## Scope and authoritative basis

GOV.UK's [Standard Visitor application information](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa)
was checked on 2026-09-07. Its conditional information includes the payer's name
and address. The current profile had personal sponsor name, relationship and UK
location but no address. This addition covers **explicitly supplied personal
sponsor addresses only**. It does not claim complete payer intake or cover an
employer/school payer through a personal-sponsor field.

The same official page separately describes parents' identities (if known),
conditional employer and partner details. These remain missing product scope,
not fields that can be treated as optional merely because the implementation
does not yet collect them.

## Implemented

- Bounded `CaseProfile.sponsor_address`, separately persisted from applicant home
  and UK accommodation. Exact source evidence uses the normal event/provenance
  path; the profile JSON includes it without a invented default.
- Shared provider extraction instructions name the field and preserve literal
  spelling and an owner-identifying sentence. No actual model call in this work.
- A dedicated owner/value guard requires an explicit current sponsor-address
  statement. It rejects unrelated homes, host/relative addresses without sponsor
  attribution, quotation, uncertainty, hypothetical framing, invented components
  and source excerpts that omit the address owner. This is not postal validation.
- Sponsor replacement, relationship/name changes and switching to self-funding
  retire inherited address facts/evidence. Same-event new explicit address remains.
- Customer PDF summary labels it separately and shows inapplicability for other
  funding modes. Chinese fact label supplied; no new automatic question yet.

## Evidence and limitations

The first targeted run failed **6 / 151**: a broad existing hypothetical-word
pattern treated the literal street word “Example” as hypothetical intent. The
fix removes the literal address only from that intent check, never from source
or value validation. After additional owner-excerpt/quoted-CJK checks the group
passed **154 tests** (0.49s). These are deterministic/captured workflow tests,
not a model accuracy or naturalness score.

The PDF skill required a real renderer preview and visual inspection. The one
page in `output/pdf/sponsor-address-v1/summary.pdf` covers English, Chinese,
missing and self-funded states; no clipped or unreadable rows were observed.
SHA-256: `0dc400fd458aa8a996a6248b6f3f46e633f3f6a42e8a047e9654136f73e270d0`.
Poppler initially warned about its default fontconfig but completed; the actual
PNG was inspected, not inferred from the exit status.

Historical profile-comparison tests explicitly expect the new field to be null
when the old report did not supply it. No historical report is overwritten and
no real case receives a fabricated address.

The first wide run (launched before the final source-excerpt/quotation checks)
reported **9 failed / 4,978 passed / 2 deselected**, 101.44s. All nine failures
were exact historical profile comparisons missing the new null field. Their
test expectations now state that absence explicitly; this is schema migration,
not a claim that the old provider supplied or tested the new field. A separate
full run is used for the final source state rather than treating an in-flight
test process as proof of edits made after it started.

Final development-wide run: **4,990 passed / 2 deselected**, 104.22s, one existing
Starlette warning. The two exclusions remain the stale source-bound consultant
and financial provider reports; fresh provider evidence is still required.
Ruff and strict Mypy (92 modules) passed. This does not close the intake or
release gaps listed below.

## Required next integration, not yet complete

1. Natural question/answer intake including valid short answers bound to the
   actual SENT question and current sponsor identity, plus explicit uncertainty
   memory so an unavailable address is not repeatedly requested.
2. Personal-sponsor conditional completeness gate and normal missing-detail
   guidance. **The field is not yet a mandatory gate; this branch is not ready
   for release on the strength of this foundation.**
3. Employer/school or multiple payers need correctly owned separate information;
   do not silently force them into a single personal sponsor.
4. Broader natural EN/ZH phrasing, exact address corrections, real-provider and
   full multi-turn acceptance, finalpack source/review binding and Gmail checks.

No real emails, service restarts, operator approvals, GitHub pushes or deployment
were performed. The complete delivery goal stays active.

## Subsequent conditional-intake integration

Personal-sponsor cases now require an address with minimum location detail in
`required_profile_facts` / `profile_fact_complete`. The field is also included
in critical-fact provenance checks. Self-funded cases do not require it. Silence
or explicit uncertainty never satisfies that condition.

EN/ZH question planning asks one address question after sponsor identity/location.
A short literal address is accepted only with a matching actual SENT question,
same recipient/thread, most recent sent payload, an earlier timezone-aware send
timestamp, and the same named sponsor/relationship identity. A model cannot set
the trusted context flag through its patch. Independently sourced full statements
remain supported without a preceding question.

The bounded short replies “I need to check”, “I don't know”, “not sure” and their
implemented Chinese equivalents record source event, source text, question event
and sponsor identity. The missing field is deferred without repeated questions,
not marked supplied. A later explicit address removes the active deferral.
Changing sponsor identity/replacing the sponsor or moving to self-funding clears
the active question/deferral binding while retaining the historical evidence.

Address receipts now repeat the grounded address, and a non-date deferral no
longer produces a dates-only waiting instruction. Tests exercise actual captured
sends and SQLite reopening for acceptance, uncertainty, later supply, unsent and
future questions, wrong sponsor binding, identity change, and final-gate state.
The focused group passed **34 tests** (0.73s); Ruff and strict Mypy passed.
Before the final receipt adjustment, the full development suite passed **4,999
tests / 2 deselected**, 101.05s with the existing Starlette warning.

The receipt-adjusted run reported **5,000 passed / 1 failed / 2 deselected**,
100.62s. The remaining old test deferred every required field but expected a
dates-only message. Its expectation now requires the broader truthful waiting
message, rejects the dates-only claim, and still checks no repeated question and
no final confirmation. The focused pacing/intake group then passed **107 tests**
(0.86s). Historical outcomes are retained, not presented as an initially green run.

Final development regression: **5,001 passed / 2 deselected**, 101.99s, one
existing Starlette warning. The two source-bound provider-report exclusions are
still outstanding acceptance work, not passes. This result does not close the
specific repeated-question review follow-up below or the wider delivery scope.

Remaining: wider natural phrasing (including contextual resumption), accurate
multi-payer/parents ownership, employer/school payer intake, displayed deferred
status in finalpack QA, and fresh source-bound provider/Gmail acceptance. A
single sponsor address is not evidence that two separate payers share it.
No live provider call, real email, approval or deployment was made here.

Code-review follow-up: `question_event_ids` can retain the previous SENT question
alongside a newly sent one. The contextual-address matcher currently requires
exactly one matching SENT row, so a legitimate short answer after a new sponsor
is asked the same question may be rejected. Reproduce this with two actual
captured sends and bind to the latest question event (not only identical payload
text) before calling this repeated-question path accepted. The existing
different-sponsor negative test is not positive coverage for that journey.
