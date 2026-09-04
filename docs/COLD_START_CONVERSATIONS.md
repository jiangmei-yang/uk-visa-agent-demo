# Cold-start conversation experiment

This experiment tests the customer's actual outgoing text, not an isolated extraction
or an unused model draft. It is not a naturalness score or an independent-user study.

## Protocol fixed before the first provider run

- Independent author: four new fictional six-email journeys, without reading production
  code, prompts, previous conversations, or evaluation results. Two development journeys
  are available for investigation; two holdout journeys remain unread until an explicitly
  authorized first holdout run. Both splits contain Chinese and English conversations.
- No prefilled customer profile, attachments, test instructions in customer messages, or
  real email recipients. Each turn uses MIME ingestion, the production extraction guard,
  workflow, automatic reviewed Gmail rendering, and the outbox dispatcher.
- The Gmail adapter captures the request locally instead of sending it. A successful
  capture becomes SENT through the real dispatcher, so subsequent questions use the
  actual sent-message ledger. This does **not** test Google networking or real receipt.
- Reopen SQLite and reconstruct the model/workflow/sender each turn. After a turn, reopen
  again and verify the same event causes no new model call, send, or state change. These
  are new instances/connections, not separate operating-system processes.
- Use real DeepSeek extraction with the production two-attempt guard and SDK retries
  disabled: at most 24 provider attempts for 12 selected turns. Retain failed first
  attempts and all later conversation consequences. Do not repair a profile mid-journey
  or rerun the whole experiment to select a successful sample.
- Rendering is deterministic, matching the installed reviewed automatic sender. The
  probe omits unused model prose calls; its token totals are **not full production cost**.
- Save raw responses, each attempt's prepared input, usage/errors, actual guarded patch,
  pre-send draft, captured body, persisted outbox/state, and replay outcome. Missing usage
  is unknown, not zero. Create reports exclusively with mode 0600 and checkpoint them
  atomically. Freeze corpus, input, schema and source hashes in the report.

## Automated checks versus judgement

The automated state checks compare cumulative explicit facts, remembered date deferrals,
pause status, and absence of unrequested consent/delivery. Human labels `self-funded` and
`self-employed` map only to the existing storage values `self` and `self_employed`;
the original independently authored corpus is unchanged. All other watched values are
compared directly, with country aliases normalized by the existing location utility.

Text checks are deliberately labelled lexical proxies. Mentioning an official URL or
words about a document's purpose does not establish that the answer is correct, complete,
relevant, or natural. A factual acknowledgement is not a repeated question. A general
checklist is not necessarily a request to upload documents.

Read every captured conversation using these dimensions, without a fabricated overall
percentage:

1. **Answer relevance:** does it answer each current question, including comparisons and
   subquestions, rather than merely naming the subject?
2. **Accuracy and grounding:** distinguish official requirements, preparation suggestions,
   and unresolved individual judgements; check links against the stated claims.
3. **Memory:** retain facts and corrections across reconstructed instances; do not ask again
   for a known birthday or a date the customer has explicitly deferred.
4. **Useful next action:** explain what to do, where to do it, and why it helps when relevant;
   do not force intake when the customer is only asking a question or has paused.
5. **Conversational fit:** acknowledge important changes without repeating a full dossier;
   avoid generic receipts, irrelevant caveats, and a questionnaire at the end of every reply.
6. **Boundaries:** no invented facts, booking/approval guarantees, implied human verification,
   unauthorized restart, consent, evidence acceptance, or final delivery.

Fix development defects with local regression tests, retaining the original provider
report. An offline replay must be separately labelled and cannot be counted as new model
evidence. The first holdout is not a tuning set; any failure remains recorded. None of
this replaces a consenting participant's unscripted ordinary-email trial or the pending
ordinary-document-to-final-ZIP acceptance test.

## Results

The [first development report](../eval_output/cold_start_development_2026-09-04.json)
retains the entire failed run. Corpus and production/evaluator source hashes remained
unchanged during execution. Eight real extraction attempts returned usage: 27,085 input,
2,492 output, 29,577 total tokens. Seven turns reached extraction, six accepted the first
attempt; the seventh failed both guard attempts. No Gmail network calls were made.

- Automated composite: **4/12** turns; captured-SENT flow: **7/12**; duplicate/reopen
  invariants: **12/12**. These are different denominators and not an accuracy percentage.
- Chinese initial consultation: the model correctly proposed `document_checklist`, but
  the sender asked three intake questions instead of explaining ordinary document types.
- Chinese employment-letter question: the model proposed `document_checklist` for a
  single-document purpose question. The sender produced four personal missing-document
  requests. Its broad document-purpose proxy passed; full reading found the response
  missed the income-versus-employment/leave subquestion and changed the requested scope.
- Chinese bank question: the answer did cover ownership, source and accessibility, but
  did not explain the balance-certificate comparison directly. Its accessibility proxy
  also failed to match `是否可以使用`; retain that lexical false alarm separately from the
  real missing subanswer, without retroactively changing the result.
- Chinese official entry/button, pause, and translation-while-paused turns were directly
  answered; previous dates and birthday were not repeatedly requested.
- English first email: both raw responses correctly preserved `8 July 1992`. The local
  value normalizer could not canonicalize that format, and the guard rejected it. The
  resulting human-review state held the next five emails without extraction or replies.
  Their missing provider attempts must not be fabricated in a later offline replay.
- Additional uncovered risk: a country-only residence statement was stored as
  `current_address`. This experiment's ten-field state oracle does not validate address
  completeness. Retain it as an open precision issue, not a passing-address claim.

A separate synthetic regression also reproduced employment-document questions being
misrouted to restrictions on working in the UK. That is a local reproduced defect, not
the exact reply captured in the development run above.

## Repairs, replay and first holdout

The first repairs add complete English month-name normalization with applicant-ownership
checks, permit general document-type overviews without a completed intake, distinguish an
individual document's purpose from a full collection request, and explain financial-record
comparisons. They do not change consent, evidence acceptance or automatic final delivery.

The [strict development replay](../eval_output/cold_start_development_replay_2026-09-04.json)
made zero provider calls. The Chinese conversation can use its saved responses; the first
English birthday now validates. However, the original run has no provider attempts for the
next five English emails. The replay records exhaustion and later input divergence instead
of inventing replies. It is incomplete, not a new successful end-to-end conversation.

The [first untouched holdout](../eval_output/cold_start_holdout_first_2026-09-04.json) was
opened only after those repairs and source freeze. Twelve real extraction attempts returned
40,702 input, 2,619 output, 43,321 total tokens. All 12 passed the guard on their first
attempt, all 12 produced one captured-SENT reply, and duplicate/reopen invariants held.
The automated composite was **6/12**, not 12/12:

- Both opening overview paraphrases still received only intake questions.
- An English employer-letter purpose question and a Chinese itinerary-purpose question
  still received the generic unsupported-answer fallback.
- Two lexical false alarms remain recorded: Chinese bank accessibility wording, and a
  conditional official-process description misread as an unsolicited document request.
- Independent full reading found another real issue that the composite missed: Chinese
  turn 3 answered a translation question and then appended a four-item upload checklist.
  English departure correction was accurate but used a system-field name and ISO date.
- Dates, facts, pause/FAQ separation and the recorded corrections worked in these selected
  journeys. This does not establish performance on arbitrary real-world conversations.

Post-holdout repairs are explicitly **exposed regressions**, not another unseen success:
recognize general overviews independently of one fixed checklist phrase; require the object
of a list question to be documents rather than, for example, translator details; avoid a
private intake questionnaire after a general overview unless the customer separately asks
to proceed; and support the exposed individual-document purpose paraphrases. No holdout
rerun or revised first score is claimed.

The implementation still uses bounded reviewed replies, not unrestricted model-generated
advice. Unsupported expressions, repeated unanswered intake questions, overly formal
correction receipts and address completeness need more work. Independent user evaluation,
ordinary documents and recipient-side delivery remain outside this experiment.
