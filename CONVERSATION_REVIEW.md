# Conversation review — 2026-09-04

Evidence class: internal review of generated replies plus automated regressions, not independent
user validation. The owner's direct feedback established that the original first reply was too
mechanical. Passing safety checks did not establish naturalness.

## Changes now covered

The [preparation pause/restart contract](PREPARATION_CONTROL.md) adds a persisted distinction
between “I do not know the dates yet” and “put the whole preparation on hold.” Paused customers
can still ask for information without triggering more intake questions. Its provider evaluation
and real-mail acceptance must be reported separately from the older tone experiments below.

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

## Follow-up prose comparison: improvements and remaining failures

`eval_output/gmail_model_prose_comparison_2026-09-04-v2.json` retains the single follow-up run after
the richer brief. All eight mechanical checks again passed because actual captured sending remains
deterministic. Six raw drafts were rejected by the existing exact-action guard; only two passed.
Reading shows fewer generic introductions in Chinese, but continued form-like English replies,
an unsupported "no documents are needed" statement, and a claim that all further steps wait for
summary review. Prompt changes alone did not establish acceptable natural conversation.

Question matching now tolerates prose punctuation/case/whitespace variation, including an Oxford
comma. This tolerance is limited to questions: grounded answers, document labels, discrepancies and
correction acknowledgements remain exact. Numeric punctuation and all words are retained, so this
does not allow different amounts/dates, dropped negation or different requested information. Added
bounded rejection checks also stop the observed document-waiver/global-pause claims even if all
required questions are present. Eight new regressions pass; all 401 local tests, lint and typing pass.

The retained v2 report predates these guard changes, and no third provider run or live model-prose
rollout is claimed. Broad paraphrase validation and acceptance of natural model-written Gmail replies
remain unfinished; this narrower formatting tolerance is not presented as solving them.

## Birthday retention and useful adviser guidance, 2026-09-04

The owner's screenshots showed a birthday supplied in dotted year-first notation being requested
again. Read-only inspection found one persistent case, not a new case per reply. The later Chinese
date spelling had already populated its birthday, accommodation and budget. Private names, dates,
email bodies and addresses are not reproduced in these public fixtures.

The old `has_calendar_day` did not recognise dotted dates and silently dropped even a correct
model proposal. An initial excerpt-only fix then failed a real fictional DeepSeek extraction:
the provider returned `1998.5.12` as the value, not ISO. The failed proposal and both failure modes
are retained in `eval_output/birthday_guard_failure_2026-09-04.json`. The final fix canonicalizes
only explicit, valid year-first values, then still checks original quoted evidence. Ambiguous
day/month order, invented years and invalid calendar values are not normalized into accepted facts.
Replaying the retained proposal and one new real extraction both accepted the expected ISO date.
Four integration cases cover ISO/raw dotted/Chinese values, reopening SQLite, corrections retaining
superseded evidence, no birthday/date re-asking, and duplicate-event/no-resend behaviour.

The service also lacked useful preparation information. It now gives small, reviewed, source-linked
answers about official application steps, application/decision timing, translation and booking,
plus case-specific preparation suggestions for self-funded students and conferences. Unsettled
routes get the official checker instead of a claimed visa requirement. These are dated, bounded
answers, not live web research or unconstrained immigration advice. The sources were directly
checked on 2026-09-04, with recheck required after 2026-10-04:

- https://www.gov.uk/check-uk-visa
- https://www.gov.uk/standard-visitor/apply-standard-visitor-visa
- https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk

Each case stores advice topic/event references. A topic is suppressed on subsequent turns only
when its outbound event was actually `SENT`; this does not claim the recipient read it. Pending
drafts do not count. Explicit questions can request an explanation again. Declined links and pure
later-reply messages do not trigger unsolicited guidance. Replies carrying a substantive answer
ask at most two missing fact fields; official answers are also retained in confirmation replies.
New birthday, accommodation, budget and travel-date facts receive an acknowledgement rather than
the generic introduction. No facts, requirements, consent or release gates are weakened.

Two real-model, captured-Gmail runs are retained. The first,
`eval_output/gmail_adviser_guidance_2026-09-04.json`, passed the old mechanical checks but **failed
manual usefulness review**: guidance was accidentally waiting for route confirmation. That is not
a successful guidance run. Conditional preparation information no longer waits for, or grants,
route confirmation. The updated evaluator explicitly checks the application link, answer-before-
questions order, student-funding explanation and non-repeated application introduction. The second,
`eval_output/gmail_adviser_guidance_2026-09-04-v2.json`, passed all eight fictional turns under those
checks. Both use real DeepSeek extraction but captured transport and reviewed prose, not Gmail
network sending or model-prose acceptance. Subsequent local edits removed a duplicated disclaimer
and acknowledged newly received travel dates; they are covered locally, not by a third provider run.

Independent internal reading still found unanswered name/birthday questions repeated across turns
and the enum phrase "employer or school" instead of preserving "school". These remain experience
defects; the reviewer supported only a controlled incremental release. This is not an independent
customer usability study or a perfect-naturalness claim. The complete local suite passes 501 tests,
ruff and mypy (55 source files), with the existing Starlette/httpx deprecation warning.

The registered-sender Gmail worker was reloaded under its existing state-directory lock. New PID
53498 completed an idle cycle at 2026-09-04T10:16:07Z; a separate process check confirmed liveness.
The existing one-case database and sent-message history were preserved. No historic messages were
manually replayed or resent, and the optional guarded-draft flag remains off. This proves loading
and polling; the new wording has not yet been observed in a new recipient-side Gmail exchange.

## Sent-aware pending questions and specific payer wording, 2026-09-04

The next experiment addresses two defects retained in the prior review, rather than calling the
earlier eight mechanical passes complete. A case now distinguishes missing facts, unanswered
questions actually sent to the customer, and the stable question plan for this reply. Correcting
the trip or asking a different question does not automatically repeat the unanswered identity
form. An explicit request to continue selects one missing field. Supplying those details later
still updates the original case and allows the next preparation step. The wording model cannot
reopen a paused intake question; requirements, evidence, confirmations and release gates are
unchanged. Pausing is not marking facts complete, giving consent, or scheduling a reminder.

The question ledger keeps a sent event reference and the latest candidate reference per field.
An unsent/failed draft does not mean the customer was asked. Legacy snapshots can recover the last
question set only from the matching sent workflow reply. A new migration regression exposed that
the workflow message hash differs from the outbox row ID; matching now reconstructs the workflow
hash from the event and plan. This regression failed before the correction, then passed. Existing
cases are migrated only while processing a new eligible event, not by manually rewriting live data.

`funding_wording.py` refines display labels from current accepted funding evidence, not from a
student's occupation or arbitrary words elsewhere in the mail. Direct statements such as school
payment can be displayed as school payment, without the combined internal enum. The helper rejects
visible negation, quotation, previous arrangements, partial/mixed funding, low-confidence,
superseded and conflicting evidence. It changes no fact or proof status. Its strict whole-excerpt
matcher still falls back to the broad category for unsupported longer phrasing, and it cannot
reconstruct context omitted by the upstream extractor. These remain explicit limitations.

The six new conversation integration scenarios were also run against baseline `2e710f6` by loading
its four relevant source modules in a temporary process without changing the worktree: three passed
and three failed (correction re-asking, unreviewed-summary re-asking, and three-field resumption).
The updated implementation passes all six plus the legacy-upgrade regression, reopening SQLite
on every turn and checking exact captured body, persistence, no consent and replay/no-resend.

`scripts/gmail_conversation_probe.py --semantic-intent --question-frontier --output NEW_REPORT.json`
adds two turns to each language: explicit resumption and a later dotted birthday/name response.
The single real DeepSeek run is retained in `eval_output/gmail_question_frontier_2026-09-04.json`.
All 12 fictional turns passed, including specific school/university wording, no repeated pending
questions on turns 3/4, one-field resumption, later identity retention and duplicate-event checks.
Reading the actual captured replies confirmed the third reply also acknowledges the new dates.
This uses real extraction, reviewed wording and captured Gmail transport, not real sending.

Before release, an independent state-flow review found another combination missing from the
12-turn report: an unsent question draft followed by a sent pure "I'll reply later" acknowledgement
incorrectly associated candidate questions with the receipt. The service now selects an empty
question plan for that receipt and records no new question references. The added regression checks
the subsequent turn really asks those still-unseen questions. This was a pacing defect, not a release
gate bypass; it was corrected before restarting the live worker.

The local suite has 601 passing tests, with lint and typing checks passing (56 source files).
Naturalness is still not fully accepted: acknowledgements can be terse and the next fresh question
batch can still contain three fields. No independent customer usability score, Gmail recipient-side
proof for this wording, broader live outage proof, or ordinary-material final ZIP is claimed.

The registered-sender worker was reloaded under its state lock after these checks. PID 55039 was
confirmed live and completed an idle cycle at 2026-09-04T10:26:59Z. The same one-case/eight-sent-reply
state was preserved across this reload. No historic mail was replayed or manually resent, no sender
scope was expanded, and the optional guarded-draft transport remains disabled. Recipient-side
validation of the new pacing is still outstanding.

## Natural questions must receive useful answers — 2026-09-04

A new internal, two-turn CaptureGmail audit reproduced another real product defect: after official
guidance had already been sent, natural follow-ups such as “申请网页在哪”, “网址发我一下”,
“材料要准备些什么”, “流水要几个月” and “签证费多少钱” could fall through to a waiting receipt.
Having guidance text in the code was not proof that customers could retrieve it. Another failure
put “wait until you can provide details” before an otherwise correct answer or document list.

The reviewed FAQ now recognizes natural application-link questions. A bare short “send that link
again” requires the case's official application guidance to have actually been SENT; arbitrary
school/hotel websites and quoted or declined requests do not count. Explicit document-list requests
accept common Chinese word order after sufficient route/person context is known. Replies begin
with the requested answer/list, not a contradictory waiting introduction, and do not reopen already
answered birthday/date questions. The same case, question ledger and evidence guards are used.

Two additional bounded topics give concrete value: the ordinary six-month Standard Visitor fee
listed on GOV.UK (£135 as checked on 2026-09-04), and the financial-evidence guidance's focus on
accessible funds and their source rather than a universal bank-statement month count. Non-visitor
routes are separated; expired guidance withholds concrete facts pending recheck. Hotel/flight
wording was softened to the official body's “less useful evidence”, not an absolute prohibition.
The source URLs are the same official application and supporting-document pages above; this is
dated reviewed content, not an always-current live retrieval service.

58 new tests cover these requests, declined/quoted/off-topic variants, expiry, route boundaries,
and the actual workflow/AutomaticGmailReplySender path with local captured sends and reopened
SQLite. Together with existing FAQ/guidance checks, 105 focused tests passed. The first captured
pipeline run found two document-list failures; both were fixed and the failing cases retained.
These assertions do not constitute an independent naturalness score or Gmail recipient evidence.

The extended provider probe is reproducible with:

```bash
uv run python scripts/gmail_conversation_probe.py --semantic-intent --question-frontier \
  --adviser-followups --output NEW_REPORT.json
```

The single retained run `eval_output/gmail_adviser_followups_2026-09-04.json` passed 20 fictional
turns (ten per language). It adds four follow-ups per language to the original six-turn sequence:
application webpage, repeat link, fee/statement period, and a natural document-list request.
All eight added turns preserved the complete profile from turn six, produced an answer before
intake, avoided waiting introductions/repeated identity/date questions, persisted the exact
captured body and replayed without another send. Real DeepSeek extraction used 39,362 input and
1,312 output tokens; wording remained reviewed and Gmail transport was captured, not external.
This is one model run, not a reliability distribution or a naturalness score. Manual reading still
found overly long repeat-link replies; the subsequent short-link wording refinement is covered
locally, not retroactively claimed as part of that provider run. Original report bytes are retained.

That refinement now sends just one sentence plus the same official link for an already-SENT,
unambiguous repeat-link request; asking how to apply still gets the process. Three further process
variants and strengthened short-reply assertions bring the focused FAQ/guidance suite to 108 passes.
The full current suite passes 719 tests, ruff and mypy (56 source files), retaining the existing
Starlette/httpx deprecation warning. These counts include the separately documented reviewed-revision
work and are not measurements of response quality.

After these checks, the registered Gmail worker was reloaded under its existing state lock. PID
57965 was separately confirmed live and completed an idle cycle at 2026-09-04T10:45:12Z. A fingerprint
over the stored case snapshots and outbound IDs/bodies/statuses matched exactly before and after
reload: one case, eight SENT replies. Schema migration assigned legacy rows revision 1. No old mail
was replayed, no manual mail was sent, and no sender scope or guarded-draft setting was changed.
This proves safe loading/polling, not recipient-side acceptance of the new replies. General open-
ended advice, nontechnical reviewer usability and real ordinary-document redelivery remain open.

## Semantic questions: classification is not adviser quality

The next iteration adds bounded question topics to the existing extraction call, not a second
autonomous agent or model-written legal advice. Natural requests can select application entry,
timing, translation, booking, ordinary visitor fees, financial evidence, or a case-specific
document list. An unsupported question is acknowledged rather than answered with an invented
rule. Invalid intents do not discard an otherwise grounded birthday or other fact.

An independent agent froze 36 bilingual fictional cases before the implementation was evaluated:
28 development and eight reserved holdout. `eval_output/adviser_semantic_development_2026-09-04.json`
retains the first 28 real DeepSeek extractions. Raw and validated topic sets matched all 28 expected
sets; the initial selected content/sender checks also passed. This used 72,399 provider tokens,
temporary SQLite cases and captured Gmail sends, not real mail. The report is not overwritten.

Reading the actual replies found defects those checks missed: an unrelated university website
question received a visa brochure; some unchanged updates did too; human-review and unsupported
warnings repeated; a passport-return question was answered with only decision timing; and a
friend-translation question got requirements without directly addressing the implied assumption.
Consequently that mechanically passing report is **not** an adviser-quality pass.

Proactive guidance now needs actual intake progress or a current preparation request, not merely
an old profile plus an unsent advice topic. Quoted/refused/control text cannot itself supply that
request. Explicit question topics take priority over unsolicited advice. A legacy follow-up test
now explicitly requests preparation in its setup; its unsent-draft case no longer expects an
unseen link to be treated as shared context. The SENT/no-resend assertions are preserved.

Source checks on 2026-09-04 distinguish decision timing from passport handling. The
[Standard Visitor application page](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa)
currently describes same-day passport return at the appointment; where a passport was actually
left with the centre, the
[processing-time guidance](https://www.gov.uk/guidance/visa-processing-times-applications-outside-the-uk#when-your-application-processing-time-ends)
says to wait for contact before returning. This is not a personal collection or courier deadline.
The [visitor document guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
requires a complete, independently verifiable translation and the listed translator details;
it does not establish acceptance or rejection merely because a friend made it. These explanations
share the existing guidance review deadline; they are not live retrieval on every customer turn.

The development replays retain the original raw model patches without additional API calls:
`adviser_semantic_development_2026-09-04-v2.json` still passed its checks but revealed default
three-item document requests after unrelated messages. The stricter v3 report failed one timing
question because a date-deferral acknowledgement displaced the answer. V4 passed all 28 selected
checks after quiet/advice-only turns stopped issuing unsolicited missing-document lists and
pure FAQ answers stopped repeating deferred-date reminders. Explicit lists now explain what each
item supports and where to obtain it, include policy source URLs, and respect declined links.
These are development results, not unseen or independent customer acceptance.

The single, first holdout run is retained in `adviser_semantic_holdout_2026-09-04.json`: seven of
eight topic sets matched (raw and guarded); micro precision was 8/9 and recall 1.0. One unrelated
school-library question was confidently misclassified as visa-related `unsupported`, producing
an irrelevant application-boundary response. The excerpt was genuine, illustrating why the guard
cannot establish semantic correctness. The other selected safety/content checks passed for all
eight; no new confirmation, pack, applicant mutation or real mail was produced. This used eight
API calls and 20,752 tokens. The holdout was not tuned against or repeated, and is **not a full pass**.

The separate fresh 20-turn bilingual provider run
`gmail_semantic_adviser_multiturn_2026-09-04-v2.json` passed its selected checks with 51,468 tokens:
date uncertainty, later exact dates and dotted birthday, corrections, pending-question pacing,
requested application links, fees/evidence answers and durable captured-body/no-resend checks.
Its earlier report with two fixed-phrase failures remains unchanged. Neither run sent real mail.

After these provider runs, final regression review restored an ordinary preparation-turn date
acknowledgement separately from a pure FAQ: current-turn `proactive_guidance_offered` distinguishes
an offered preparation step from an answer. It defaults false and resets each new event; it grants
no fact or release authority. An old exhausted-fields test now explicitly asks for the next step,
instead of requiring a date reminder on a silent turn. These final presentation refinements are
covered locally, not retroactively claimed as measured by the frozen holdout. The full suite
passes 860 tests, ruff and mypy (56 source files), with the existing Starlette/httpx warning.

Remaining limitations include irrelevant responses to some off-topic questions, formulaic quiet-
turn openings, a bounded reviewed answer catalogue, and no independent recipient naturalness
acceptance. Topic accuracy on this small corpus is not general accuracy, and no perfect-score
claim follows from the regression count. A future off-topic improvement needs new unseen cases;
the now-exposed holdout must not be relabelled or reused as fresh evidence.

The incremental candidate was loaded into the existing registered-sender Gmail service under its
state lock, without enabling model prose, widening sender access or automatic final-pack release.
New PID 60803 was separately confirmed live and completed an idle poll at 2026-09-04T11:07:55Z.
The raw case/outbox fingerprint matched before and after: one case and eight SENT replies. No
historic mail was replayed or manually sent. This is a safe-loading observation, not a new customer
reply, recipient-side usability result, or evidence that the failed holdout issue was fixed.

## New scope corpus and reply-level review

The next independent corpus has 24 development and eight reserved holdout cases, with evaluator-only
expectations for explicit applicant corrections. Original messages/labels/splits were frozen before
measurement; the correction annotations did not change them. See `evals/README.md` for fingerprints.
`off_topic` now distinguishes unrelated requests from UK-visa questions outside the reviewed answer
catalogue (`unsupported`). This follows the explicit out-of-task handling recommended in the
[structured-output guidance](https://developers.openai.com/api/docs/guides/structured-outputs#handling-user-generated-input),
not a claim that a schema can prove meaning. DeepSeek remains JSON Chat plus local Pydantic validation,
not OpenAI strict-schema enforcement. No model, temperature, endpoint or token limit was changed.

The first real development run, `adviser_scope_development_2026-09-04.json`, is retained unchanged:
22/24 cases passed the selected checks, with 24 API calls and 65,200 provider tokens. Twenty-three
extractions were schema-valid; one added forbidden `value: null` keys to date deferrals. The other
failure proposed an unnecessary whole-document checklist for a question about obtaining bank
statements. Raw and guarded exact-topic results were both 22/24 including the schema failure;
micro precision was 23/24 and recall 1.0 among available classifications. No real mail was sent.
The schema error occurred in this probe's single extraction before workflow capture; it is not
evidence that the installed worker failed to reply. Production's bounded retry/review fallback is
covered separately and must not be silently substituted for the failed measurement.

Independent engineering reading of every actual reply found further problems, including a PASS
that answered a bank-statement question with visa decision timing and omitted online acquisition.
Both generic work/medical responses were safe abstentions but gave no useful verification step.
Other observed weaknesses include repeated route disclaimers/links, field-like English correction
wording, and asking for a passport in a checklist without acknowledging that the applicant said
they already have it. Possession must not be confused with submission or verification. These
observations are not independent customer usability acceptance, and the passing cases are not
therefore declared adviser-quality passes.

Development changes clarify deferral object keys and the distinction between a document list and
obtaining one document. Bank answers now add a practical collection step when asked: look for an
official electronic statement or request it from the bank. That suggestion is not a guarantee of
acceptance and is separate from the reviewed financial-evidence explanation. Timing keyword
fallback cannot expand a classified bank-coverage clause into a visa-decision answer; separate
timing questions remain answerable.

The [permitted-activities page](https://www.gov.uk/standard-visitor) and
[medical-visit page](https://www.gov.uk/standard-visitor/visit-for-medical-reasons) were checked on
2026-09-04. Bounded contextual verification replies now point to these pages without deciding
whether an individual's work or treatment plan qualifies. They share the existing 2026-10-04
review deadline and are suppressed for an independently stated different route. Declining links
removes source lines, not the rest of a multi-question answer.

Scope-only turns keep existing case details and an unchanged, actually-SENT summary context;
independent corrections, files, date deferrals, natural confirmations and requests to continue
still use normal workflow checks. Review flags, sender mismatch, old events, finalization and
held-update restrictions precede or exclude this path. Distinct grounded boundary excerpts are
preserved even when they share a topic, so a second unrelated application-fee question cannot
escape its scope just because the first unrelated question was already recorded. These are
bounded code/contract guarantees, not proof that the model always finds the correct scope.

The second real development run (`adviser_scope_development_2026-09-04-v2.json`) used 24 calls
and 66,962 tokens. All extractions were schema-valid; two genuine unrelated questions were omitted
and got quiet receipts instead of scope responses. Exact topic sets remained 22/24, with precision
1.0 and recall 21/23. One additional content failure was an evaluator defect: a fixed 40-character
negation window misread “not a guarantee that any downloaded file will be accepted” as a promise.
The corrected evaluator tests scoped negation without allowing an unrelated earlier “not” to
excuse a later affirmative guarantee. The original report remains failed and unchanged.

The final development candidate (`adviser_scope_development_2026-09-04-v3.json`) used 24 calls
and 67,852 tokens. All extractions were schema-valid, but only 21/24 topic sets and selected case
checks passed (precision 1.0, recall 20/23). Two unrelated requests and one genuine English
document-list request were omitted; their receipts were not adequate answers. Both explicit
corrections were retained. The bank subquestions were answered without irrelevant visa timing,
work/medical questions received their reviewed verification steps, and an explicit “carry on”
request now received preparation guidance despite a separate date deferral. These improvements
do not cancel the three omissions or establish improvement in general model accuracy.

Preparation resumption now evaluates whole current sentences rather than vetoing a message
because “later” appears in a separate date statement. Comma-linked hypothetical/negative clauses,
quoted requests and link opt-outs remain excluded. The full suite passes 1,179 tests, ruff and
mypy (56 source files), with the existing Starlette/httpx deprecation warning. Those are local
regressions, not a reason to call this conversational candidate complete. The candidate was frozen
after these checks for its first new eight-case holdout measurement; no exposed holdout was reused.

The single new holdout run (`adviser_scope_holdout_2026-09-04.json`) passed all eight expected
topic sets and selected checks, with eight API calls and 23,040 tokens. Both held-out explicit
corrections reached their expected profile values. The measured source hashes match the final
development candidate. No real Gmail send, applicant confirmation or final pack occurred.
These eight inputs are now exposed and cannot be reused as unseen evidence. This small holdout
pass does not override the three development omissions or the remaining formulaic/low-information
replies. The conversational acceptance remains **incomplete**.

The incremental test candidate was loaded into the existing registered Gmail worker under the
state lock (PID 64418), with reviewed wording, sender scope and final-send restrictions unchanged.
This reload was **not** a state-preserving idle experiment: a new ordinary all-materials request
arrived at 11:35:29 UTC and was replied to at 11:35:56 UTC, increasing the outbox from eight to nine
SENT replies. Its five-item response explains each document's purpose and includes official
application/document links. Read-only authenticated Gmail GETs confirmed the SENT copy's exact
persisted body, recipient, thread, reply header and no attachments. A following idle poll retained
one outbox row for that event. See `eval_output/gmail_scope_live_reply_2026-09-04.json`.
This proves one real ordinary-message reply, not recipient-side reading, approved naturalness,
correction of the development omissions, or final-pack delivery. No manual message was sent.

## Single-call input correction and reply-content gaps

The combined system prompt requested questions as well as facts, but its old user wrapper said
“Extract only facts.” An independently authored 36-case corpus and controlled three-arm experiment
tested that conflict before adding more orchestration. Full design, hashes, usage and all failures
are in [QUESTION_UNDERSTANDING_EXPERIMENT.md](QUESTION_UNDERSTANDING_EXPERIMENT.md). The neutral
single-call wrapper matched 24/24 new development topics, 24/24 exposed scope-regression topics
and 12/12 topics in a single fresh holdout. The baseline matched 22, 22 and 12 respectively; the
focused two-call architecture matched 24, 23 and 11 after guards. All specified corrections remained
intact. This selects the smaller request correction, not a generally superior model/agent claim.

Manual reading found automatically passing but unhelpful replies: a funds-access question received
months boilerplate; a birthday correction triggered an application tutorial; a date receipt continued
preparation after the customer said not now. All three arms also missed the practical answer to
“where I can obtain them.” Content changes now explain relevant bank records and acquisition,
bind financial wording to financial question/context clauses, distinguish identity corrections
from preparation progress, and remove the unsolicited continuation from date receipts. The
English birthday receipt no longer exposes the title-cased internal field label.

Independent synthetic tests caught additional mistakes during that change: negated multiple
accounts were incorrectly stated as a fact, “cover the costs” was mistaken for record-date coverage,
and an employment-letter question leaked into bank-acquisition guidance. Conditional wording and
question-specific context fixed these without changing facts or release gates. All 28 content-gap
tests pass, including normal-topic deduplication with a second explicit bank subquestion, declined
and quoted requests, and identity correction plus an independent request to continue.

The first zero-model-call replays are retained unchanged: new development 23/24 and old scope
regression 22/24 selected checks. Those reports exposed two real bank subquestion omissions after
ordinary-topic deduplication and an evaluator false positive: “not confirmation that ... will be
accepted” was misread as a guarantee. The oracle now respects that explicit negative scope while
still rejecting a separate later affirmative promise. It is not a reason to discard the first runs.
The second replays passed 24/24 each. A third pair, after retaining relevant account context and
removing repeated financial-check explanations, also passed 24/24 each. Every replay has zero new
model calls and immutable source-report/current-code fingerprints; no holdout was replayed.

The final local suite is 1,339 passing tests, ruff and mypy (57 files), with the existing single
Starlette/httpx warning. A persistent pause/resume workflow, richer unsupported-route handoff,
link deduplication, wider subquestion coverage and independent recipient naturalness remain open.
The less pushy date receipt is not a claim that an application pause was persisted.
