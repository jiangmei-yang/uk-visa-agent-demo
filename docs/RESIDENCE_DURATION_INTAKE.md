# Current-home residence duration: staged implementation

Status: **incomplete, unreleased**. GOV.UK application information checked
2026-09-07 explicitly includes the current home address and how long the applicant
has lived there: [official application information](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa).
This does not justify deriving an exact move-in date from an approximate duration.

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

- Add paced EN/ZH duration questions and short-answer grounding tied to the
  actually sent question, not merely a model's requested-field hint.
- Retain uncertainty without fabricating a duration or repeatedly asking.
- Add the field to required fact/provenance/final confirmation gates for all
  applicable cases, including old cases. Do not default old cases to an invented
  duration just to preserve passing fixture outputs.
- Update fictional seed email/PDF inputs explicitly before migrating dependent
  completed-intake fixtures, and validate the clean container demo again.
- Include readable labels/values and unknown states in customer summaries and
  output artifacts, then visually verify any changed PDFs.
- Test simultaneous address/duration corrections, broad real-model phrasing and
  address identity/formatting cases; refresh source-bound live reports.

The field is currently **supplied-only and not a new release requirement**.
Consequently the current release gate does not prove this official information
has been collected. This is a known acceptance gap, not optional application
information or a finished application pack. The overall delivery goal stays open.
