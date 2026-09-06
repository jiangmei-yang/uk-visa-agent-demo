# Personal sponsor address: supplied-fact foundation

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
