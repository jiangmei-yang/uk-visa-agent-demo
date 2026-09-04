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

The English wording has not received an equally comprehensive conversational review. Deferral
recognizes a conservative set of explicit date-uncertainty phrases, not every paraphrase or every
field. Unsupported/free-form policy questions still need broader sourced-answer coverage. Naturalness
across all real multi-turn conversations is not proven; external user observation remains required.
