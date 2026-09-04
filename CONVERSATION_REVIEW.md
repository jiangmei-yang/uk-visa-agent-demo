# Conversation review — 2026-09-04

Evidence class: internal review of generated replies plus automated regressions, not independent
user validation. The owner's direct feedback established that the original first reply was too
mechanical. Passing safety checks did not establish naturalness.

## Changes now covered

- First enquiry: explains why context is needed and asks purpose, passport country and application
  location in ordinary prose, without mentioning internal pack-release rules.
- Continuing intake: work/study and funding questions precede name/DOB, since these determine the
  relevant material categories. Newly extracted purpose/occupation/funding can be acknowledged;
  unknown facts are not invented to make the reply sound personal.
- Undecided dates: an explicit statement of uncertainty defers those questions while other missing
  facts remain. Dates stay unknown, the delivery gate remains blocked, and supplied dates are not
  erased. Deferral survives saved-state reload and duplicate email replay.
- Correction: acknowledges the actual changed values without restarting the introduction.
- Attachments/conflicts: preserves named documents and specific discrepancies; generic review holds
  no longer describe documents as an internal completion counter.
- Human-review hold: states uncertainty and the need for manual checking without pretending the
  application can proceed or promising that a human has already picked it up.
- Final confirmation: asks the customer to check names/dates and describe corrections in their own
  words. The suggested “已核对无误” expression is covered by recognition tests and still requires
  the current delivered-summary context; negative/conditional replies cannot release a pack.
- Final handoff: tells the customer what to inspect while retaining the human-review and
  not-submitted boundaries. It does not assert approval or actual recipient delivery.

## A failure found by reading, not by the score

`eval_output/conversation_pacing_2026-09-04.json` passed its eight synthetic scenario runs, but
manual reading found that merely mentioning an unbooked hotel triggered an unsolicited policy
paragraph. That is a conversational failure even though the cited answer was grounded. The trigger
now requires a booking-specific question. The first report is retained, not overwritten.

The follow-up `eval_output/conversation_pacing_2026-09-04-v2.json` passed all eight
real-model runs. Reading the replies confirmed the unsolicited sourced booking paragraph no
longer appeared, but several responses still use formulaic reassurance and list-led questions.
This is not a naturalness pass for every conversation. These reports exercise model-rendered
workflow replies; the registered-sender automatic Gmail service instead sends deterministic
reviewed wording. The two output paths must not be presented as the same live-mail evidence.

The local regression suite passes 222 tests, with lint and strict typing passing. Pacing tests
include saved-state reload, duplicate-message handling and unchanged release boundaries.

## Remaining work

### Follow-up: contextual reply polish

The deterministic wording used by automatic Gmail replies now names newly received attachment
files instead of reporting only a count, omits a repeated greeting when acknowledging an attachment
or correction, translates corrected Chinese enum values, and presents a single remaining question
as prose. These changes do not declare attachments accepted or change confirmation/release rules.
Four new regressions cover Chinese/English attachment acknowledgement, translated corrections,
and a single outstanding question. The full local suite now passes 301 tests, with lint and typing
passing. This is code-level evidence, not a new real-email or independent naturalness observation.

The English wording has not received an equally comprehensive conversational review. Deferral
recognizes a conservative set of explicit date-uncertainty phrases, not every paraphrase or every
field. Unsupported/free-form policy questions still need broader sourced-answer coverage. Naturalness
across all real multi-turn conversations is not proven; external user observation remains required.

## Live follow-up: Outlook quoted history

A registered sender's ordinary follow-up on 2026-09-04 retained Outlook's From/Date/To/Subject
quoted reply in `latest_customer_message`. No false release was observed, but the old message
was still exposed to extraction as current text. The reply-boundary parser now recognises complete
English and simplified/traditional Chinese header blocks, with optional Cc. A lone From/To line
is retained rather than silently discarding applicant prose. New tests verify that quoted assent
cannot release a pack and that quoted fact changes never reach the workflow's extractor. This
is a bounded header-pattern fix, not a claim to understand every email client's quoting format.
The complete local regression suite passes 308 tests after this fix; lint and strict typing pass.

The Gmail worker was restarted under the existing state lock to load the contextual wording
changes. Its next completed poll retained two processed events, two SENT replies, and zero packs;
no replay or replacement of old replies was performed. These counts are a dated observation,
not expected permanent service totals. No mailbox addresses or message bodies are published here.

## Automatic confirmation scope regression

The Gmail automatic sender regenerates deterministic wording before sending. An internal audit
found that its fallback renderer treated `awaiting_profile_confirmation` as final confirmation,
unlike the model guard's normal renderer. Consequently an information-only confirmation included
the current-documents heading and said the next step was assembling the pack, even though the
workflow still needed supporting documents. This was misleading wording, not observed gate bypass.

Before the fix, the new sender-level regression failed for both Chinese and English profile
confirmation; final confirmation passed. The renderer now preserves the profile-only flag. The
four language/stage cases check the captured provider-bound body, persisted outbox wording and
non-repetition on a second dispatch. All 312 local tests, lint and typing pass after the fix.
These use a capture adapter, not new recipient-side observations. Final-pack automatic dispatch
remains disabled in registered-sender service mode, as documented in GMAIL_AUTOMATIC_SERVICE.md.

## Bilingual multi-turn model probe

Three eight-turn provider probes are retained at `eval_output/multiturn_context_2026-09-04*.json`.
They use fictional text and temporary databases, not real mail or attachments. Manual reading of
the first mechanically passing run found repeated questions after an explicit request to reply
later. A narrow deterministic acknowledgement now handles pure later-reply messages in both the
guarded and automatic wording paths. It cannot suppress new facts, corrections, attachments,
grounded answers or blocking issues, and does not set consent or schedule reminders. Other wording
still contains repetitive reassurance and form-like questions; naturalness is not fully accepted.

Runs 2 and 3 failed exact country-name checks. Run 3's new profile snapshots show Chinese aliases
of the expected locations; run 2 lacks enough data to resolve its values. The evaluator and domain
location comparison now recognise a bounded alias set without changing original facts. All three
reports and this oracle limitation are retained; see `evals/README.md`. Local regressions after
these changes pass 329 tests, lint and typing. No new recipient-side naturalness evidence is claimed.

## English continuity and question grouping

A code/read-through review found that `received_context` returned nothing for English: newly
provided work/funding details were ignored in the opening, which instead repeated generic reassurance.
English now acknowledges only supported newly received purpose/work/funding facts, as Chinese does;
unknown values and older profile facts are not invented or restated as new. Short follow-up questions
retain their exact grounded content but use prose instead of a questionnaire heading and bullet list.
Document lists and discrepancy lists are unchanged so actionable details remain easy to inspect.

Reading the actual rendered example then exposed repeated uncertainty instructions for arrival and
departure. When both are currently requested, one question asks for both dates and the year; missing
facts, deferral and confirmation state are unchanged. If only one date is missing, its existing question
is retained. This grouping is shared by the guarded renderer and the deterministic Gmail fallback.

Five added regressions cover bilingual follow-ups, English factual continuity and bilingual date-pair
grouping. The first three failed before the implementation. One existing test counted bullet markers;
it was updated to assert all three specific questions and their count instead, preserving its original
next-three-fields/no-internal-code contract. All 350 local tests, lint and typing pass, with the existing
Starlette/httpx deprecation warning. This is internal wording review and local regression evidence,
not independent naturalness acceptance or a new live recipient observation.
The existing registered-sender Gmail LaunchAgent was reloaded under its state lock, and its new
process completed an idle poll at 2026-09-04T09:32:05Z. No old replies were manually resent; this
runtime observation establishes loading/polling only, not how a new recipient judges the wording.

## Mixed-intent Gmail reply acknowledgement

Two provider-bound capture tests (Chinese/English) reproduced a missing acknowledgement: an
`if/elif` opening selected a correction and skipped files and new work/funding information in the
same turn. The deterministic blocked reply now composes all three applicable acknowledgements,
without restarting the greeting or repeating a second generic "Thanks". It continues to report
receipt, not document acceptance. The cases contain fictional extracted state and filenames;
these tests do not exercise extraction or upload actual attachments.

The tests verify the exact automatic Gmail sender body, persisted outbox wording, unchanged case
snapshot, no repeated funding question and no second send on redispatch. The initial tests failed
on missing filenames; after the fix an English assertion needed case-insensitive matching because
the new sentence correctly starts "You're". All 352 local tests pass. This is simulated sender
evidence, not a live-mail or independent naturalness pass. Further WhatsApp expansion is deferred
per the owner's Gmail-first priority; no download-grant prototype was retained or deployed.

## Requested checklist before completed intake

An internal review found that missing profile fields consumed the entire three-item reply budget,
so an explicit request for a document checklist could receive only more questions. Two bilingual
regressions reproduced an empty document list despite known purpose, passport/application location,
occupation and funding. A bounded explicit-list request now shows the currently applicable outstanding
requirements before the remaining questions, with a note that changed arrangements may change the
list. Requirements still come from the existing policy engine; the renderer neither creates new
requirements nor marks them complete. First enquiries without that context retain paced intake.

Three negative controls cover declined lists and quoted old requests. All five new tests preserve
the case snapshot and/or existing pacing; the positive cases remain blocked by the delivery gate.
All 357 local tests, lint and typing pass. This is local renderer evidence, not a live extraction or
Gmail delivery result. The current sender-scoped live mailbox still had only two processed messages
and two SENT replies when inspected before this change, with no new applicant turn to evaluate.

## Real extraction through captured automatic Gmail dispatch

`scripts/gmail_conversation_probe.py` ran two fictional four-turn conversations through real
DeepSeek extraction, the workflow, durable outbox and `AutomaticGmailReplySender`. Gmail requests
were captured locally: no mailbox, recipient, attachment or delivery API was exercised. Unused model
prose generation was replaced by the existing deterministic renderer, matching the automatic sender's
wording choice rather than spending on a draft that it overwrites. Reproduce with a configured key:

```bash
uv run python scripts/gmail_conversation_probe.py --output eval_output/gmail_conversation_probe_new.json
```

The retained `eval_output/gmail_conversation_probe_2026-09-04.json` has all eight turns mechanically
passing: checklist request answered, undecided dates deferred, purpose/funding/date corrections applied,
conditional assent not released, exact body persisted and duplicate replay not resent. This is a
bounded single run, not an extraction accuracy estimate or proof of complete final delivery.

Reading the body still found two conversational defects: an English accommodation question exposed
the internal field wording, and an explicit "haven't checked the summary" caveat received a fresh
generic introduction instead of an acknowledgement. The English question now asks where the person
plans to stay. During blocked intake, a bounded unreviewed-summary phrase is acknowledged before
retaining the missing questions; it does not alter confirmation or suppress corrections/documents.
Three local regressions cover these adjustments. All 360 tests, lint and typing pass. The original
provider report is retained unchanged and predates these wording fixes; no second provider run or
independent naturalness pass is claimed.

## Material categories explained for the applicant

On 2026-09-04 the official [supporting-document guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
was checked directly, particularly sections 2, 3 and 6. It describes circumstance-dependent evidence
and specifically addresses organiser invitations for conferences; its examples must not be presented
as one mandatory checklist for everybody. This review does not extend the policy's freshness window.

The reply renderer now labels the existing status requirement for the known student/employed/
self-employed branch, identifies the conference organiser's invitation, and distinguishes self-funded
evidence from an organisation's explanation of costs covered. An explicitly requested list states
that it is case-specific preparation, not a universal mandatory checklist. The underlying evidence
alternatives, requirement applicability and release gates are unchanged. Six bilingual regressions
verify the tailored labels and unchanged case state; all 366 local tests, lint and typing pass.
This is a source check plus local wording evidence, not a new provider or applicant validation.

## Ordinary live Gmail follow-up: coarse trip plans

A new registered-sender message was processed at 2026-09-04T09:46:31Z and a third SENT reply
was recorded. It described a half-year travel horizon, roughly a week away, and no concrete plans.
The response immediately asked for exact arrival/departure dates again. This is an observed live
conversation defect, although provider SENT alone still does not establish recipient-side receipt.

The deferral recognizer now handles a bounded Chinese half-year horizon together with a separate
clause saying there is no concrete plan. Four regressions cover two variants, no date invention,
budget uncertainty not being mistaken for date uncertainty, and preservation of existing exact dates.
All 375 local tests, lint and typing pass. The existing live case/reply was not edited or replayed;
this fix has not yet been observed in a subsequent live conversation. Broader paraphrase coverage
and carrying newly recognized older uncertainty into an already-running case remain to be checked.

## Repeated date questions reported by the owner

Recipient screenshots and the private live case disproved the completeness of the previous fix:
the plain phrase "日期没定" did not match the recognizer, and the saved case had no deferral flags.
An additional planner fallback reintroduced deferred fields once other questions were exhausted.
Both are application-level defects; the automatic sender uses deterministic wording, and the
model extraction schema does not itself control question deferral. No model training or fine-tuning
has been performed.

The recognizer now covers bare uncertainty and tested English variants, excludes quoted history
and unknown birthdates, and the planner never automatically reintroduces deferred questions. On a
new event, the workflow first recovers deferral from the saved latest customer turn, then processes
new facts; known dates remain untouched and newly supplied dates clear deferral normally. This
supports the existing case without editing its database or replaying the already-sent emails.

Eleven added regressions cover phrase variants, negatives, exhausted questions and a legacy Gmail
case across three new turns and database reopenings. The sender-level test checks extraction context,
persisted next-question fields, provider-bound bodies and replay non-repetition. Its first stub
omitted required CasePatch fields and correctly fell into human review; the stub was corrected,
not the guard. All 386 local tests, lint and typing pass. New live follow-up behavior still needs
recipient observation; earlier SENT replies and the owner's failure evidence remain unchanged.

## Model-understood question deferral, not model-controlled release

CasePatch now has an optional bounded `question_deferrals` channel. DeepSeek is asked to infer
temporary travel-date unavailability from meaning, with a verbatim excerpt and confidence. The guard
discards low-confidence, ungrounded or quoted-history evidence. Only arrival/departure fields are
allowed; the workflow applies the intent only to missing dates, persists deferral and never grants
confirmation or changes a known date. Legacy replies without the optional field remain compatible.
Gmail wording still uses the reviewed deterministic renderer; this is not free-form model sending.

Six local regressions plus the updated schema contract cover restricted targets, evidence checks,
known-date preservation and persistence. All 392 tests, lint and typing pass. The previous schema
contract failed on the newly added field and was updated to assert the narrower intent schema,
not to allow arbitrary workflow-state proposals.

One real DeepSeek run is retained in `eval_output/gmail_semantic_intent_2026-09-04.json`:

```bash
uv run python scripts/gmail_conversation_probe.py --semantic-intent --output eval_output/semantic_intent_new.json
```

All eight bilingual turns passed their declared checks through captured Gmail dispatch. The two
indirect university-holiday messages do not trigger the keyword recognizer when tested alone,
but the model-backed workflow deferred both dates, answered the checklist request and retained
later corrected exact dates. No actual Gmail API send or real applicant document was involved.
This single bounded run does not prove arbitrary intent understanding; semantic misclassification
can still affect pacing, although it cannot bypass release requirements.

## Model prose versus the automatic sender

`scripts/gmail_conversation_probe.py --semantic-intent --model-prose --output NEW_REPORT.json`
now retains the raw model draft, guard result/fallback reason and captured final automatic body
separately. It makes real DeepSeek rendering calls as well as extraction; no Gmail API is invoked.
The single retained run is `eval_output/gmail_model_prose_comparison_2026-09-04.json`.

All eight mechanical workflow checks passed, but that is not a prose acceptance result. Seven drafts
passed the existing rendering guard; one English draft was rejected because question paraphrases
did not preserve required literal strings. Manual reading found repeated introductions, form-like
lists, a claim that all progress waits for confirmation even while requesting information, and
wording suggesting that the selected next questions were all that remained. Automatically replacing
every draft is not the sole cause of poor naturalness; simply enabling raw drafts is not justified.

The brief now supplies follow-up state, grounded newly received context and deferred-question fields,
with explicit instructions against restarting introductions or treating a selected step as complete
requirements. One contract test covers this metadata; all 393 local tests, lint and typing pass.
The provider report predates these brief changes and remains unchanged. These new prompt instructions
have not yet had a second real-model run. Automatic Gmail sending remains deterministic, and no
worker reload was needed for this offline comparison; this is not a live prose rollout.
