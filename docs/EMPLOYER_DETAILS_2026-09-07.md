# Employer details: supplied-fact foundation

## Why this work is needed

The GOV.UK application-information page previously reviewed in this project lists
employer address and telephone among circumstance-dependent details. The profile
could store occupation but could not separately retain those details. This work
adds that missing representation; it is **not complete employer intake or a
release checkpoint**. The applicant's employer is not automatically a trip payer.

## Implemented

- Separate bounded `employer_name`, `employer_address`, `employer_phone` profile
  values and ordinary per-field event/source evidence, persisted through SQLite.
- Shared provider instructions preserve literal values and a complete sentence
  identifying the applicant's current employer. No country code, translation,
  geocoding or company verification is inferred.
- A current-owner guard supports explicit English/Chinese statements and rejects
  former/other-person employers, quotations, hypotheticals, clipped ownership,
  unknown values and fabricated phone prefixes. The generic other-person guard
  delegates employer facts to this dedicated guard, not an unconditional bypass.
- Changing a previously known employer name clears old address/phone unless
  independently supplied in that same event. Moving away from employed status
  retires all active employer profile facts. Old evidence remains superseded in
  history rather than being deleted.

## Evidence and limitations

The first targeted run was **19 passed / 4 failed**: the generic other-person
filter rejected the current employer before the dedicated guard ran. After
integration it passed 23 tests. Three additional negative full-guard tests first
failed because their test events omitted required `received_at`; that fixture
construction was fixed, not production validation relaxed.

Final focused group: **83 passed** (1.67s). Full development regression:
**5,046 passed / 2 deselected**, 101.08s, one existing Starlette warning. Ruff and
strict Mypy passed for 93 modules. The same two stale source-bound consultant and
financial provider reports remain excluded, not treated as release evidence.

Historical exact-profile tests now explicitly expect the three new fields as
null when absent from old reports. No report, real customer profile or evidence
is backfilled with invented employer data.

Pending required integration:

1. Natural question planning, actual-SENT short answers, uncertain/unavailable
   detail memory and corrections; current support is explicit supplied sentences.
2. Circumstance-dependent completeness gates, appropriate handling of self-employed
   applicants and employer/school sponsorship without conflating roles.
3. Explicit “changed jobs” with an unnamed old/new employer, multiple employers,
   and contact/identity binding across those changes.
4. Telephone extensions and broader company/address phrasing; current telephone
   syntax is deliberately limited to literal 7–15 digits plus common separators.
5. Changed-employer document applicability, customer-facing labels/receipts,
   inapplicable/deferred output states and full PDF/JSON/source consistency QA.
6. Fresh model, operator-review, real Gmail and release acceptance.

The new fields are not yet mandatory gate fields and no automatic employer
question was added. Generic profile serialization can expose them, but finalpack
visual/applicability acceptance is not claimed. No model call, PDF authoring,
real mail, service restart, operator approval, deployment or GitHub push occurred.
