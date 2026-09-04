# Case-aware advice, not another intake form

Gmail remains the primary realistic test channel. The model is an API-connected DeepSeek model,
not one trained by this project. It proposes facts, questions and pacing preferences; deterministic
workflow code owns the case record, confirmation and release. This iteration adds useful next-step
information without making a question into permission to resume, confirm or deliver.

## Reply contract

- `next_step` is a current information request about this applicant's visitor preparation. It is
  separate from `preparation_intent`, which expresses a whole-preparation pause/resume preference.
- After applying this message's validated corrections and documents, a pure selector chooses one
  missing detail or one outstanding material. It names the material, explains what it supports,
  gives an acquisition/submission step and links the reviewed official source where appropriate.
- Undecided dates are not invented or repeatedly asked. Already received/processing files are
  checked rather than requested again. Actual conflicts, review holds and stale policy take priority.
- A paused customer's requested preview is conditional information, not an upload demand. A current
  summary request comes before asking for another file; an already confirmed summary is not reconfirmed.
- FAQ answers and the selected step survive reply rendering together. Asking for one item no longer
  triggers the whole checklist by keyword fallback; an independently requested checklist is preserved.
- The existing question ledger records only the single question actually emitted. Per-turn advice
  resets on the next accepted message, persists across restarts, and does not repeat on duplicate events.
- Automatic Gmail formatting is tested using a capture adapter. Its transmitted body matches the
  stored reviewed reply; these local captures are not new recipient-side delivery evidence.

The reply selector cannot change facts, document acceptance, pause state, consent, epochs or pack
permissions. Official application submission remains the applicant's responsibility. PDF instructions
describe this demo's email attachment support, not a universal government-only-PDF requirement.

## Independent corpus and first results

`evals/next_step_cases.json` was independently authored without production code, prompts, earlier
corpora or outputs. It contains 24 development emails (12 Chinese/12 English) and eight initially
reserved emails (four each). SHA-256:
`c6ed70c1ff8387618127e383d7d220e86382d78c68cbc702948b77004ccaeead`.
All are fictional, with a fixed adult self-funded student profile, undecided dates and no documents.
The labels and rationales never enter the model input. All eight reserved inputs are now exposed.

`scripts/next_step_probe.py` makes one default extraction call per input, disables provider retries,
checkpoints raw results/errors/usage and then uses captured patches in a real isolated SQLite workflow
with network calls disabled. There are no real email sends. Development replay checks original
report/corpus/input/label hashes and makes zero API calls; it cannot silently replace failed output.

| Retained run | Raw checks | Guard checks | Workflow checks | Meaning |
|---|---:|---:|---:|---|
| First development | 19/24 | 19/24 | 20/24 | Original failures and full replies retained |
| Development replay | Not a new model result | 21/24 | 23/24 | Saved original responses, no API calls |
| Development replay v2 | Not a new model result | 21/24 | 23/24 | Further reading fixes; still no API calls |
| First holdout | 6/8 | 6/8 | 6/8 | One attempt per case, no retry or selective removal |

Reports are `eval_output/next_step_development_2026-09-04.json`,
`next_step_development_replay_2026-09-04.json`,
`next_step_development_replay_2026-09-04-v2.json`, and
`next_step_holdout_2026-09-04.json`. The first development run used 83,175 tokens; holdout used
28,219, totalling 111,394 across 32 calls. Mean extraction latency was 1.100 seconds and 1.234
seconds respectively, not a service guarantee. No API/schema errors were observed in these calls.

## What the failures actually mean

1. **Application guard errors, development 12/13:** DeepSeek correctly proposed resuming the
   preparation previously put on hold. Our guard mistook the historical pause modifier for a current
   pause or historical whole sentence. The repaired guard preserves surrounding negation, conditions
   and third-party context. Original raw outputs now resume correctly in zero-call development replay.
2. **Evaluator error, development 03:** the actual reply contained the full case-specific checklist
   and the selected first item. The original evaluator incorrectly demanded a separate FAQ answer.
   The new oracle requires every applicable outstanding item's explained label in the actual outbox;
   an empty list or one missing item fails. The original report is unchanged.
3. **Label ambiguity, development 10/11/22/23:** the frozen null-action labels conflate no state
   transition with no expressed preference. Explicitly maintaining a pause can legitimately propose
   `pause` without incrementing the epoch. Strict original labels/scores remain intact; the independent
   reviewer identified this ambiguity rather than relabelling successful-looking outputs.
4. **Model scope error, development 24:** a different UK visa question became `off_topic`, producing
   the inaccurate claim that it was outside UK-visa preparation. The prompt boundary was clarified,
   but saved-output replay still fails this case; it is not evidence that a new model call fixed it.
5. **First holdout 04:** the model understood “take ... off hold,” but the guard discarded `resume`.
6. **First holdout 07:** a question about a brother's student visa included a wrongly accepted
   `next_step`, causing advice to be selected from the original applicant's visitor case. No facts,
   consent or pack were released, but the advice was contextually wrong. Safety invariants alone
   cannot make this a conversational pass.

Holdout 01 also differs on the raw pause label: “keep the previous pause” proposed `pause` while
the frozen oracle expected null. Its guard/state checks passed. Equal aggregate counts across
layers do not mean the same cases failed; inspect the per-case records.

## Post-holdout local repairs

The first holdout remains **6/8**. After exposing its failures, the current code adds the explicit
“take ... off hold” construction with negated, future and third-party counterexamples. The
case-aware advice boundary also checks whose application the current request refers to before
ordinary-topic deduplication: a sibling's request must not occupy the sole `next_step` slot and
discard a later independent own-case request. Related background alone is not a case switch.
This is a bounded scope check, not a general coreference parser or proof of complete understanding.

`test_exposed_first_holdout_failures_are_local_regressions_not_a_new_score` uses the two original
failed raw patches in the real local workflow. It asserts the original records still say failed,
does not call a model or send email, and tests no implicit confirmation, pack, fact leakage or
duplicate effects. Passing these exposed regressions is repair evidence, **not** a replacement
holdout score or proof of the next unseen paraphrase.

Final local verification: **1,918 tests passed**, ruff clean and strict mypy clean for 59 source
files, with one existing Starlette/httpx deprecation warning. The final development-only replay
`eval_output/next_step_development_post_holdout_replay_2026-09-04.json` still reports 21/24 guard
and 23/24 workflow under the unchanged original labels. It makes zero API calls and preserves
the original errors. Final source-bundle SHA-256:
`4782399d200d188f8770b99e2acec7949772ba0944ce5093400388231440777a`.
The earlier holdout/replay-v2 source hash was
`6131156fd1818c2d7a31b1dfea64546257d2f8afdcc20c67c6855c492ec9c1d1`;
the post-holdout repairs mean those are deliberately different versions.

The registered-sender Gmail service loaded the final source with unchanged configuration and
case/outbox projection (one case, nine SENT rows). PID 74488 completed an idle cycle at
12:59:46 UTC; no manual mail, historical replay, state reset or automatic final-pack dispatch
was performed. See `eval_output/gmail_next_step_reload_2026-09-04.json`. This is deployment
evidence, not a new recipient-side conversation or an update to the separate Docker UI.

Reading also found an unsolicited brochure and internal English field labels on a DOB/budget
correction, redundant file-history wording with no files, and a missed registration-order subquestion.
The correction now receives a concise factual acknowledgement without another date reminder.
The first replay still missed the registration subquestion because a declined step in another clause
suppressed it; replay v2 preserves the separate request. The answer explicitly says the account order
has not been verified rather than inventing it. The current language-selection page and English-answer
requirement were checked against the [official entry](https://visas-immigration.service.gov.uk/apply-visa-type/visit)
on 2026-09-04. Full account-creation/form navigation is **not** validated.

## Reproduction and limits

```sh
.venv/bin/python scripts/next_step_probe.py --corpus evals/next_step_cases.json \
  --split development --output NEW_PROVIDER_REPORT.json
.venv/bin/python scripts/next_step_probe.py --corpus evals/next_step_cases.json \
  --split development --replay-from eval_output/next_step_development_2026-09-04.json \
  --output NEW_REPLAY_REPORT.json
```

The first command spends API usage; the second does not. Existing paths are refused. Do not present
another run of these now-exposed inputs as a new holdout. Exact topic/control checks, source links and
local tests do not prove universal naturalness, immigration accuracy, identity authenticity, production
reliability or independent usability. Ordinary-document final delivery and revised-pack recipient
acceptance remain open in [the capability ledger](VALIDATION.md).
