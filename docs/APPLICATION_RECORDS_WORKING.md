# Application records: active implementation, not released

Working branch: `codex/application-records`. The running Gmail service and released
main branch remain on the preceding editorial release. This is not a completed
intake feature, nor proof of real-recipient email or final-pack acceptance.
The overall acceptance goal remains active.

## Requirement and design boundary

### 2026-09-07 reply integration checkpoint (development only)

The collection planner is now called from the blocked DRAFT reply path after
scalar intake is answered/deferred, with no competing question/material action.
Case JSON persists `collection_question_event_ids`; only corresponding SENT
outbox events suppress a later prompt. Unsent draft metadata does not count as
having asked. Questions have EN/ZH reviewed text, quote the saved record identity
for missing details and allow uncertainty without invented exact dates. Detail
prompts must match the current ledger/revision. No processing grant, profile
confirmation, release permission or real mailbox action is created by this code.

First development full regression exposed **14 failures / 4,768 passes / 2
deselections** (99.66s): an over-eager collection prompt interrupted quiet replies,
correction-only messages and existing confirmation context. This was a behavior
regression, not a reason to relax those tests. The trigger now requires a current
record change, a supported continuation or an answer to a pending scalar question,
and preserves the existing confirmed-profile flow while the universal gate is
unfinished. The five affected integration files now pass **185 tests** (7.86s).

The new captured-SENT integration case seeds a fictional late-intake profile,
allows material guidance first, proves a quiet receipt does not start questions,
then accepts an explicit no-UK-contacts statement and asks about past travel.
The next reply declares uncertainty; after SQLite reopening it does not reask
travel or ask for UK contacts that were explicitly denied. This is a real local
workflow/guard/outbox path with substituted extraction and transport, not proof
of provider interpretation, consent provenance for seeded facts or live Gmail.

Still open: contextual short replies and per-field deferral memory, collection
priority versus repeated document actions, universal intake before profile/final
confirmation (including legacy cases), applicability/provenance checking and
replacement of the development release hold. This checkpoint must not deploy or
be described as a complete collection-to-ZIP journey.

Final development regression for the reply integration: **4,785 passed / 2
deselected**, 97.20s, one existing Starlette deprecation warning, recorded in
`/tmp/visa-collection-plan-regression-v2.log`. Ruff and strict Mypy passed for 86
source modules. The two excluded legacy source-bound provider reports remain
stale, not refreshed or relabelled. This is not release acceptance; no fresh
provider call, GitHub CI run or live-recipient delivery was performed.

### 2026-09-07 collection-planning checkpoint (not wired or released)

`workflow/record_collection_plan.py` now produces case-bound, deterministic
follow-up identifiers for unasked/partial collections and baseline missing
record details. Explicit unknown collections are deferred, not repeatedly
queried or defaulted to none. A caller can exclude previously asked identifiers;
SQLite/JSON reload and a legacy missing ledger becoming an empty ledger retain
the same identifiers. An explicit full-list assertion does not hide missing
record details; a stale none assertion does not hide a later trip. The planner
rejects a foreign case ledger and never mutates declarations or grants release.

This is an independent planning primitive, **not yet connected to the reply
workflow or durable question history**. Baseline field coverage is not a legal
applicability check, precision check, provenance audit or completed delivery
gate. The existing development hold remains in place. Per-field uncertainty,
contextual short answers, question wording and final confirmation/ZIP integration
remain to be implemented. No Gmail process or container was changed.

Verification: 10 new planner tests; 113 passing tests across planner, declaration,
intake, captured workflow and saved-provider replay suites (0.79s). Scoped Ruff
and Mypy passed after fixing an import-order issue and explicit Literal narrowing.
No new live-provider call or full-suite/current-provider acceptance is claimed.

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
the bounded natural-language proposal planner before commands reach this layer.
This guard is deliberately not a general coreference or truth-verification system.

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

## Inbound workflow connected, release still held

`CasePatch` now carries bounded record and collection-assertion proposals. The
model cannot choose ledger IDs, hashes, consent or release states. Shared extraction
instructions cover both providers; DeepSeek's combined-response ceiling is 4,000
tokens and an explicit `finish_reason=length` is rejected even when JSON parses.
This is a ceiling, not a target token usage. Schema/provider failures use the
existing bounded retry and review path, not partial record acceptance.

After current sender/thread/processing checks, the workflow passes the latest
unquoted body through the record planner. Source scope, explicit past/current
ownership, literal field roles, correction action and unique existing target are
checked before atomic persistence. Current record values (without internal IDs or
hashes) and collection states are included as model context, never fresh evidence.
Ambiguous current corrections retain the old ledger and the current customer
message and require human review. Independent scalar updates retain their existing
guard. An exact workflow event replay performs no new extraction or send.

Local tests now exercise real WorkflowService, reopening SQLite, reviewed sender,
outbox dispatch and captured SENT bodies. They cover Chinese/English records and
corrections, uncertainty across unrelated messages, a contact distinct from an
applicant address/payer, foreign stories, ambiguous corrections, replay and no
extraction before a configured processing grant. The model proposals and transport
in these tests are substituted; this is not a live provider/recipient claim.

Manual reading of the first captured replies found identical generic receipts and
no next step: receipt text had been counted as an information answer. Receipts now
follow the actual persisted category/action/state, not rejected proposals, and do
not suppress the ordinary progressive question plan. An already asked question is
not repeated merely because the customer sends a record correction. Broader
naturalness, exact-value correction receipts and collection-specific intake remain
open; these examples are not an independent usability score.

**Temporary development hold:** any collected application record/assertion fails
`application_record_intake_release_checked`. This prevents new incomplete records
from passing the old scalar-only delivery gate while applicability, detail checks
and question planning are unfinished. It is not the requested final gate. Legacy
cases without collections still expose the original intake omission and must be
migrated through genuine applicant statements, not defaulted to none. Do not deploy
this branch until the hold has been replaced with completed universal intake and
provenance checks, corresponding fixtures, revision/outbox/ZIP tests and fresh evidence.

Failures retained in the development record: the first semantic run had 9 failures
(including the English month May treated as uncertainty and a Chinese friend's
trip treated as the applicant's). Fixes retain their negative regression cases.
The first full inbound regression had 1 failure / 4,752 passes / 2 deselections:
the schema contract lacked the newly defined proposal lists. The contract now also
checks their bounded sizes and absence of model-supplied ledger/release authority.
The next full development regression passes **4,758 / 2 deselected**, 97.91s,
with the existing Starlette warning. Ruff and strict Mypy cover 85 source files.
The two old source-bound provider reports remain excluded, not relabelled as current.

`scripts/consultant_journey_probe.py --scenario-set records` adds a four-turn,
fictional real-model development probe with captured transport, no model retry,
preserved failures, full Python/font-asset hashes and a CasePatch schema hash.
It checks exact record fields, correction preservation, unknown-state memory and
an unrelated person's trip alongside an independently supplied date of birth.
The existing `all` scenario set remains the preceding journey+pacing corpus;
the new records set is separate until its intake/release work is complete.

### First real-model record probe (failed, retained)

`eval_output/application_record_intake_2026-09-06-v1.json` binds source `f131c4d`
and 89 files (85 Python modules, this probe and three font assets), plus the
CasePatch schema. SHA-256:
`f1d18254a940428bb9c57ccbddf88589864cc2b5ef5bfc86cbeb3d21770a8ffb`.
Two turns passed, one failed and the fourth could not continue after the workflow
entered review. **Three** real model calls were made, not four: 15,006 input /
992 output / 15,998 total tokens. No retry, real Gmail call or real document.

The provider correctly extracted the first records and targeted Japan correction.
It labelled explicit uncertainty about remaining travel history as `partial`.
The initial guard escalated that ordinary statement instead of preserving the
uncertainty, so the next event was held and no fourth extraction occurred. The
guard now treats an explicitly uncertain partial-list statement as `unknown`,
without modifying the captured raw proposal or asserting absence/completeness.
Negated uncertainty retains a negative regression case.

Reading the real second proposal also exposed a redundant unchanged `country`
field. The ledger now retains the original source for unchanged values, even when
the model repeats them during a date correction. An all-unchanged amendment leaves
the material fingerprint intact while retaining replay bookkeeping. Saved raw
outputs have a pinned offline workflow/captured-SENT replay; this repair is not
retroactively a passing provider run. A new source-bound run is still required.

### Second real-model probe (also failed, retained)

`eval_output/application_record_intake_2026-09-06-v2.json` binds source `847bf43`;
SHA-256 `27a563296e3129cb1996ebd2b1c40da1d3c3f13619d865810a1061c5662db93d`.
The first turn passed. The second failed strict proposal parsing: the provider
echoed an old purpose with a current excerpt that did not contain it, and composed
a target label from several remembered values. The workflow safely entered review;
turns 3/4 made no provider calls. Two calls consumed 9,964 input / 825 output /
10,789 total tokens. Combined v1/v2 usage is five calls and 26,787 tokens, not eight
calls or two successful four-turn journeys.

The boundary has now been corrected structurally, not by accepting false evidence:
model text is represented as **untrusted proposed text**; only the planner can
construct the domain's literal `QuotedText`. A correction target is resolved from
its current verbatim source quote against case-local identifying values; a model
hint, concatenated label or guessed ID cannot select a target. Tied best matches
remain ambiguous. Fields identical to the uniquely selected existing record are
ignored as echoes and keep their original sources. Every genuinely new/changed
field still needs current literal evidence and role checks before the entire batch
commits. Unsupported new values, negated values, an all-unsupported correction,
or an explicitly targeted field represented only by an unsupported old echo all
require review. One valid change cannot hide another unsupported requested change.

The original v2 raw output now has an offline actual-workflow/captured-SENT replay,
including exact corrected period and unchanged country/purpose provenance. Negative
tests cover malicious target hints, cross-entry ambiguity and unsupported changes.
No old report was rewritten. This repair still needs a fresh source-bound provider
run and the complete intake/release work above. The pre-boundary-change development
regression was **4,765 passed / 2 deselected** (100.52s); the new boundary is being
regressed separately.

### Third real-model probe (bounded pass, not overall acceptance)

`eval_output/application_record_intake_2026-09-06-v3.json` binds source `7c8fa51`;
SHA-256 `018ca8d995305ed6bc5c8061c76e932eaad2a74d23967031569519d807f4e227`.
All four turns passed, with one actual DeepSeek call per turn and captured SENT
transport. The 89 recorded Python/probe/font hashes were compared to the working
files after the run: no mismatch. The schema hash is also recorded. This manifest
does not yet separately enumerate the policy YAML; the final release manifest
must include all policy/configuration inputs as well as code/font assets.

Usage: 20,355 input / 1,093 output / 21,448 total tokens. Across all three retained
attempts: **9 actual calls**, 45,325 input / 2,910 output / 48,235 total tokens.
No real Gmail sends, real applicant data, document uploads or workflow retries.

Implementer read every actual reply and record revision:

- Turn 1 retained two distinct past trips and one UK contact, with original partial
  date precision and no applicant-address/sponsor mutation. It asked one current
  visit-purpose question, not the deferred future dates.
- Turn 2 corrected only the Japan period. Country/purpose retained turn-1 sources;
  Korea and the contact were unchanged. The receipt is still generic rather than
  quoting the exact new period: naturalness work is not complete.
- Turn 3 retained explicit past-history uncertainty and did not demand guessed
  dates or escalate an ordinary inability to remember.
- Turn 4 ignored the friend's France trip, saved the explicitly stated birthday
  as `1997-07-01`, retained both future-date deferrals and past-history uncertainty,
  and did not ask for the birthday again. The general route explanation is useful
  at this early stage but is not independent evidence of an ideal consultant reply.

The original raw outputs also pass an offline, reopened-database/captured-SENT
four-turn replay. The post-boundary full development regression passed **4,770 / 2
deselected** (99.61s); subsequently the new v3 replay was added and the complete
saved-provider replay file passed **4 tests** (0.36s). This is not a claim that a
new full 4,771-test run was executed. Ruff and strict Mypy passed for 85 source files.

Runtime observation after this work: the preceding review-console container
`7aeb2c031629` remained healthy on `127.0.0.1:8000`; `/health` returned status `ok`.
Its image was still `sha256:e2a730ba714c14ce395d2e40d89058b755c1ff17ae32a89159212970e8638231`.
The existing Gmail process PID 61377 was alive and reported idle at
`2026-09-06T12:39:18.094537+00:00`. No service was restarted or new branch deployed.
Process/health observations do not prove receipt-side end-to-end delivery.

**Next implementation priority:** replace the temporary hold with complete,
applicability-aware collection intake, question/defer planning and provenance
gates for both new and legacy cases; then exercise confirmations, revision and ZIP
delivery with genuine fictional declarations. Do not stop at the four passing
exposed examples or narrow overall acceptance to this record feature.

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

1. Broaden the bounded proposal guard and verify real-model behavior, including
   direct short answers, same-sentence multiple trips, natural target references,
   pronoun-linked contact details, alternate date wording and correction recovery.
   Do not turn an unsupported but genuine statement into silent information loss.
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
