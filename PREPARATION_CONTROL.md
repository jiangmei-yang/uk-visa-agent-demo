# Customer-requested preparation pause and restart

This is a pacing preference, not visa eligibility, a document waiver, summary consent,
deletion or permission to send an application. Gmail remains the primary live-test channel.

## Contract

- The existing single DeepSeek extraction may propose `preparation_intent` with an enum action,
  current-message excerpt and confidence. It cannot write case state or an outbox version.
- A separate guard checks current, explicit whole-preparation intent. Quotes, conditional future
  plans, third-party requests, instructions to rewrite internal fields, one delayed document and
  undecided travel dates do not themselves pause or restart a case. The guard is deliberately
  narrow and may miss unfamiliar wording; a confidence number is not measured accuracy.
- `Case.preparation_paused` is orthogonal to human review and document validity. Facts, provenance,
  received files and current FAQs still go through their existing independent guards.
- While paused, no intake questions, confirmation request or final pack is generated. A customer
  can still ask for an official application link or a preparation checklist; the response gives
  information without asking them to send the items now. Files are received, not deemed genuine.
- A pause or restart transition increments `preparation_control_epoch`, records its event, and
  clears profile/final consent. Repeating the same preference does not increment it again.
- Restart means resume preparation only. It cannot consume a pre-pause summary or a confirmation
  mixed into the restart email. A fresh summary must be issued and confirmed afterwards.
- Every queued reply carries its epoch. Older-epoch replies, including retries of old questions,
  confirmation requests and final packs, cannot be newly sent after a transition. Current-epoch
  paused replies are restricted to the safe informational/receipt plans.
- `SENDING` remains reconcilable: if the provider already accepted it, record that fact without
  resending. This cannot recall a message or attachment already accepted by an external provider.
- Pack generation checks persisted pause/version and serializes registration with the case write.
  A stale in-memory case must not overwrite a newer pause. The release gate includes preparation
  being active; missing dates, invalid documents and all other gates remain unchanged.

## Evaluation method, frozen before provider measurement

`evals/preparation_control_cases.json` was independently written without inspecting production
code, prompts or earlier reports. Its SHA-256 is
`dee83ff18493a22a7ef354b81b999e89686dbd63a28987bfde4fe8c8c1986106`.
It has 32 fictional emails: 24 development (12 Chinese/12 English), and 8 reserved holdout
(4 Chinese/4 English). Expected control, mixed factual changes and explicit refusal-history
review labels are evaluator data, never input to the model.

The probe makes exactly one default `extract_case_patch` call per case with SDK retries disabled.
It records raw output, validation, tokens/latency/errors and code hashes. The same captured patch
then runs through isolated temporary SQLite and the actual workflow with network connections
disabled. This includes persistence and duplicate processing, but **no real email is sent**.

```sh
.venv/bin/python scripts/preparation_control_probe.py \
  --corpus evals/preparation_control_cases.json --split development \
  --output eval_output/preparation_control_development_2026-09-04.json
```

The holdout requires `--split holdout --allow-holdout` and a new report path. Run it once after
development changes are frozen; any subsequent tuning makes it exposed regression data, not
a fresh holdout. Preserve failed reports, do not silently retry failed model cases, and separate
raw-model correctness from guard and workflow failures. Synthetic tests and these small corpus
scores do not establish universal naturalness, legal accuracy or production reliability.

## First development measurement and fixes

The retained `eval_output/preparation_control_development_2026-09-04.json` contains 24 one-call
DeepSeek results: all 24 raw control/fact/review checks passed, but the guard passed only 22/24.
It rejected ordinary Chinese wording for putting everything aside, and confused continuing a
pause with continuing preparation. The workflow therefore passed 23/24 state checks: the second
case was already paused, but the first incorrectly offered preparation advice. No mail was sent.
These were application-rule failures, not incorrect DeepSeek control proposals.

The guard repairs cover the general expressions and their negative/conditional counterparts;
the model prompt was not tuned to the two examples. Additional internal reading found and fixed
unsolicited translator qualifications borrowed from unrelated friend background, active-preparation
wording inside a paused booking FAQ, a restart reply with no next step, internal English budget
labels, and missing DOB acknowledgement during human review. These reading findings were not
captured by the original intent score. New synthetic reply tests specify them independently.

The first report used 76,082 input and 2,596 output tokens (78,678 total), with a mean extraction
latency of 1.076 seconds in this run. Its aggregate `prompt_tokens`/`completion_tokens` counters
incorrectly show zero because the adapter exposes `input_tokens`/`output_tokens`. Individual usage
records and the total are intact; keep that failed report unchanged and correct aggregation in
subsequent reports. These are token counts, not a billed currency estimate or latency guarantee.

The final zero-call replay, `eval_output/preparation_control_development_replay_2026-09-04.json`,
passes all 24 guard/workflow cases using the same original model outputs. It does not establish
a new model accuracy score. The first reserved run,
`eval_output/preparation_control_holdout_2026-09-04.json`, passes 7/8 raw and guarded intent checks,
and 8/8 state-safety checks. The corpus is now exposed; it is no longer an unseen test set.

The remaining failure is `pc_en_hold_001`: a current request to keep preparation moving and help
with the next document, mixed with date uncertainty and a flight-booking FAQ. The model omitted
the continue intent and the reply answered only the FAQ. The case was already active, so it did
not incorrectly pause, confirm or release anything; nevertheless, the requested next step was
missing. This remains an open conversational completeness issue, not a pass hidden by safety.
There was no retry or prompt tuning on this holdout and its report remains unchanged.

The holdout used 25,411 input and 1,159 output tokens (26,570 total); all 32 provider calls together
used 105,248 tokens. All returned schema-valid output without an API error. Holdout mean extraction
latency was 1.282 seconds in this one run. Final local verification: **1,665 tests passed**, ruff
clean, and strict mypy clean for 58 source files (one existing Starlette/httpx warning).

Development replay, holdout and deployed source have the same source-bundle hash:
`3a02aa5ba248fc041e8474c6b3f76f9cffe18cc3433608ae9275cca61d6ecc68`.
The registered-sender Gmail worker loaded this bounded improvement with its scope unchanged;
automatic final-pack dispatch remains excluded. This is an incremental test-service deployment
with the above known gap, not acceptance of a universally reliable adviser. Deployment-only
evidence is in `eval_output/gmail_preparation_control_reload_2026-09-04.json`.

## Operational limits

Already-finalized or human-review cases retain subsequent messages for controlled review, as
before. A pause/resume phrase does not bypass that queue. No automatic promise that a human has
already read the case is made. An already-delivered pack cannot be recalled. Epoch checks do not
prove arbitrary simultaneous edits are lossless, nor do they reverse a provider send already in
flight. No ordinary recipient-visible pause/restart journey has yet been claimed by this feature.
