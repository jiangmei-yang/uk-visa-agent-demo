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
