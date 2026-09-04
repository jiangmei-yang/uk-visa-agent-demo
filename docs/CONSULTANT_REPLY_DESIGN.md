# Consultant replies: usefulness before intake

2026-09-04. This is an exposed-defect repair, not a claim of human-level advice or
universal accuracy. The customer's complaint was valid: ordinary enquiries could
receive only questions, and employment, family visits and sponsor arrangements
could receive the same generic introduction without a useful preparation action.

## Actual architecture and limits

The agent is not a model trained by this project. DeepSeek proposes structured
facts and question topics. The workflow validates and persists those proposals;
reviewed GOV.UK-based content supplies policy statements and official links.
The default automatic Gmail sender uses reviewed composed wording, not the model's
free-form draft. The optional guarded-draft mode still requires exact grounded
content. This release does **not** claim to have removed that restriction or
switched existing mailboxes into free-form generation.

This protects facts and delivery authority, but it also makes the quality and
coverage of the reviewed content critical. A low model-fallback count is not a
naturalness metric: some intentional reviewed paths never invoke the drafting
model. Better wording alone cannot repair a missing answer or missing case memory.

## Reply contract

- Give a useful answer before gathering more personal details. An ordinary first
  enquiry can receive a limited preparation orientation without first completing
  the profile. Conditional visitor guidance does not establish eligibility or the
  applicant's route.
- Explain a relevant action and its purpose: who to contact, what to obtain or
  clarify, and what that information helps establish. Employment, studies,
  self-employment, personal/organisation support and family visits need different
  advice. A host must not be inferred to be the person paying.
- Offer relevant official links, not a directory of every source in every reply.
  Count guidance as shared only after the associated reply is SENT. Explicit
  requests can obtain the link again; an upgrade alone must not repeat old advice.
- Ask at most one main intake question. Arrival and departure form one date-range
  question; both fields are recorded as asked. Name and birth date are not bundled.
  Missing facts still block final delivery and are never filled by inference.
- Keep known facts and date deferrals. Answer a standalone FAQ without silently
  restarting the form. Respect pauses, declined links and quoted/conditional text.
- Keep independent questions independent. Declining fees must not suppress a
  request for the application page. A separate student-route question must not
  replace the answer to a visitor-route question with student advice, or vice versa.

Official preparation sources were re-read on 2026-09-04: [application steps](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa),
[supporting evidence](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
and the [route checker](https://www.gov.uk/check-uk-visa).
Suggested evidence is framed as relevant preparation, not a universal mandatory
checklist, fixed bank balance or guarantee. Existing dated-source expiry checks
remain in force.

## Reproduction and evidence boundaries

`tests/integration/test_consultant_value.py` uses fictional text and fixed extraction
proposals through the real workflow, guard, SQLite, automatic Gmail sender and
dispatcher. It inspects the actual captured SENT body and reopens the store between
turns. `tests/unit/test_consultant_guidance.py` covers advice selection and suppression;
`tests/unit/test_consultant_question_scope.py` covers independent question scope.
Older tests expecting two or three unrelated questions now expect one. The
legacy-ledger test explicitly models the old two-field question record; its
sent-only memory guarantee is retained.

For a bounded provider experiment, run from the repository root:

```sh
.venv/bin/python scripts/consultant_value_probe.py --allow-model-calls \
  --output eval_output/consultant_value_NEW.json
```

This makes at most seven fictional DeepSeek extraction calls and may incur API
charges. It uses a temporary database and captured transport: **no Gmail API,
recipient email, real documents or existing applicant cases**. All attempts and
failures are retained; an output file cannot be overwritten on a subsequent run.
The checks are specified behaviours, not a percentage score for naturalness.

### First provider observation (retained failure evidence)

[The original seven-case run](../eval_output/consultant_value_provider_2026-09-04.json)
made seven real extraction attempts, one per case, with zero mailbox calls. Its
original checks printed four passes and three failures. This is **not** four
fully satisfactory answers: the two mixed-question checks were too weak and
accepted a generic second-answer boundary. Independent review caught that test
defect; a bare application link must not count as answering both questions.

The student and parents-funded examples extracted the expected profile but were
classified as `next_step`; this suppressed their useful guidance and returned a
name question. The family example omitted employment, then asked about work
despite the customer's sentence saying they worked in Hong Kong. That run did not
capture raw model proposals, so it cannot establish where that omission occurred.
The original report is not overwritten. Subsequent probes opt into fictional-only
raw proposal capture, strengthen the second-answer checks and retain each new run
separately. Retesting these exposed examples is development validation, not a new
blind holdout or independent naturalness assessment.

[The second run](../eval_output/consultant_value_provider_2026-09-04-v2.json)
retains raw fictional proposals. Its then-current checks passed six examples,
but the same first enquiry was labelled `unsupported` and got a generic non-answer.
Manual review also found a profile defect outside those checks: visiting a sister
was recorded as a sponsor relationship even though the applicant said they paid
for the trip. The reply's cautious wording did not make that stored fact valid.
Later probe checks explicitly inspect this role boundary; the original second
report and its narrower checks are preserved.

[The third run](../eval_output/consultant_value_provider_2026-09-04-v3.json)
made seven further real extraction calls. All seven then-specified checks passed:
the first enquiry received conditional official orientation, the four profile
contexts received different preparation actions, and both mixed requests retained
their independent answers. Raw proposals still show the model labelling the first
enquiry unsupported and treating a visited sister as a sponsor; the application
guards corrected those proposals before the captured reply and saved case.

Manual reading again found something the printed checks missed: the new sponsor
guard discarded the explicitly stated `parents` relationship. The family reply
also spoke as if self-funding and accommodation still needed to be established.
The follow-up repairs retain explicit current parents-support evidence, narrow
asked-context exceptions to actual short answers, and use already-known
self-funding in family guidance. Their saved-proposal replay is
`tests/integration/test_consultant_saved_replay.py`; it is **not** a fourth provider
run. All three original reports remain unchanged. Across them there were 21
fictional extraction calls (76,964 reported tokens), zero Gmail test sends and no
ordinary personal documents. This is still a small exposed development set, not
an independent naturalness score or a proof of unrestricted semantic coverage.

Release review also closed an older Gmail exact-phrase confirmation shortcut:
`PROFILE CONFIRMED` now requires the current unchanged profile summary to have
actually been SENT, just like natural confirmation. In particular, technical
document recovery cannot be followed by that phrase to skip the newly required
profile summary. The offline non-Gmail fixture shorthand remains compatible.

Independent uncoached reading and a consented ordinary-material end-to-end trial
are still needed. The processing-consent gap and other remaining technical work
are recorded in [the acceptance audit](END_TO_END_ACCEPTANCE_AUDIT.md).
