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
