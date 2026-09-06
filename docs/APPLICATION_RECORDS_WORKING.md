# Application records: active implementation, not released

Working branch: `codex/application-records`. The running Gmail service and released
main branch remain on the preceding editorial release. This is not a completed
intake feature, nor proof of ordinary-email extraction or final-pack acceptance.
The overall acceptance goal remains active.

## Requirement and design boundary

The user's original design explicitly lists UK relatives/contacts and travel
history under structured intake. The current profile has neither collection;
`has_serious_history` is a separate declaration, not ordinary travel history.
The [official application information page](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa)
also distinguishes condition-dependent travel history, UK family and payer details.
Related omissions to assess against the original scope include home-address
duration, known parent details, employer contact information and partner details.

One trip or one contact is a distinct record, not a replacement blob of text.
Each supplied field retains its exact spelling/precision, excerpt, original event
and body hash. A period such as "2023年夏天" remains that phrase: this layer does
not invent dates or silently translate a country. Unknown fields stay absent.
Contact support details never set profile funding, home address or risk flags.

Corrections use an exact record ID and current revision digest selected by a
trusted planner. They retain unchanged field sources and create a new revision.
Withdrawals are tombstones, not deletion of history. Stale/foreign targets,
reinterpreted event IDs and invalid partial batches are rejected atomically.

**A matching quote is not semantic ownership or applicant consent.** The domain
commands and `case_with_record_commands` adapter are for the trusted workflow;
they are not exposed as an LLM or external API. Current applicant ownership,
negation, hypothetical/report scope and correction intent must be validated by
the still-pending natural-language planner before commands reach this layer.

## Implemented in this checkpoint

- Typed travel/contact fields, per-field source attribution and revision ledger.
- Atomic add/amend/withdraw and event replay protection, including JSON reload.
- Case-bound SQLite persistence and rejection of foreign record ledgers.
- Material record changes alter both profile/final summary fingerprints. The
  draft adapter clears prior confirmations without resuming paused preparation.
  Non-draft cases must use the existing review/revision workflow.
- Email summary and PDF summary rows share the collected records. Current JSON
  projection retains sources, omits withdrawn records/internal batch history and
  explicitly labels collection completeness `not_assessed`.
- Explicit collection assertions now distinguish `unasked`, `unknown`, `partial`,
  `none_declared` and `complete_declared`. Empty input never means none. Each
  assertion retains its statement, event/body hash and revision chain, and binds
  to an exact case/type/record snapshot. A full-list assertion is not proof that
  record details are sufficient, verified, or ready to release.
- New/corrected/withdrawn records invalidate a prior none/full-list assertion for
  that collection. Uncertainty stays recorded until explicitly superseded; a
  change to contacts does not invalidate an unchanged travel list. Failed mixed
  record/assertion batches roll back atomically. Conflicting duplicate, stale,
  foreign and missing-source assertions are rejected. Stating none while active
  entries exist requires explicit record withdrawals, not silent deletion.
- Declaration-only changes invalidate profile and final-summary confirmations,
  including a previously empty legacy ledger. SQLite reopening retains the
  state, sources and pause preference. Exact event replay does not invalidate a
  later confirmation. These are trusted-adapter checks, not live-email evidence.

## Rendering defect discovered and repaired

The first Chinese QA render (`output/pdf/application-records-foundation-v1`)
displayed Chinese text as squares. The production PDF body/index used Helvetica.
A bundled, static Noto Sans SC font now supplies an embedded glyph subset for
text outside Windows-1252. The source commit, deterministic build command,
license and hashes are retained under `src/visa_agent/assets/fonts/`.
Unknown glyphs/control characters raise an explicit error instead of emitting a
plausible but unreadable file. This does not promise support for all languages.

Both v3 pages were visually inspected: Chinese names/date precision/address and
the long English address are readable, with no clipping or overlap. The English
PDF is byte-identical to v1. v2 is an intermediate font-build QA record; v3 uses
the final font with an unchanged source timestamp. These are fictional QA previews,
not applicant packs or evidence that the intake/delivery gate is complete.

After adding explicit collection-state rows, both v4 pages were rendered and
visually inspected again. The partial-list labels and supplied values are readable,
with no clipping/overlap. SHA-256: English
`6aab68c0e1517fb9ee580ea78224c3be62c02fdeb8bfca13eb005bac05b9c4e0`;
Chinese `d34aa539e809043576746b4e17918fd379ae247aa2e31a98d8119f7f55aceafe`.
Earlier v1-v3 renders remain historical QA evidence, not current-layout previews.

The built wheel was inspected and includes the 10,595,932-byte font, full OFL
notice and source README. The font SHA-256 is
`eeb06b8a64fd04a2744d95579db1571b51027cda61ed78c62e4b730791525461`.
No runtime font download or installed system CJK font is required.

## Verification and next required work

The focused ledger/declaration/store/summary/font set passes **81 tests** (0.35s).
Ruff and strict Mypy pass (83 source files). The preceding foundation regression
passed **4,661 tests, 2 deselected** (99.03s). The post-declaration full development
regression passes **4,696 tests, 2 deselected** (98.87s), with the existing
FastAPI/Starlette test-client deprecation warning. Both development runs exclude
the two preceding source-bound provider reports; those old reports are
not current-code evidence and must be refreshed after the complete feature is
frozen. No new paid model calls or Gmail sends were made in this checkpoint.

Before release, all of the following still need implementation and evidence:

1. A bounded model-proposal schema and current-applicant semantic guard; include
   direct answers, multiple records, third parties, quotations, hypotheticals,
   ambiguous corrections and retry/model-failure behavior.
2. Connect the explicit collection-state ledger to validated inbound interpretation
   and question planning. Silence and a summary acknowledgement cannot supply a
   missing declaration; persisted uncertainty must actually suppress repeat asks.
3. Progressive one-question intake, appropriate personalisation and safe deferred
   questions; record receipt and targeted correction acknowledgement in actual
   captured-SENT replies.
4. Delivery completeness/provenance checks for these requirements, including
   legacy cases and new ordinary messages. Do not bypass the requirement merely
   because historical fixtures did not contain the newly enforced declarations.
5. Actual event-driven confirmation, revision/outbox invalidation and ZIP tests,
   not only direct trusted-adapter/summary projection tests.
6. Broader real-model journeys, full source/font-asset-bound reports, full regression,
   clean-container deployment and GitHub CI. Do not update source hashes in old
   reports to pretend that they tested these changes.
