# Question-understanding experiment

Status after measurement (2026-09-04): the neutral-wrapper **single combined call** is selected as
the DeepSeek default. The separate focused pass remains evaluation-only. Live worker deployment
is recorded separately from source changes. This experiment does not authorize arbitrary recipients, document acceptance, personal
eligibility decisions, or automatic final-pack release.

## Why compare three alternatives

The preceding final development run retained three omitted questions in 24 cases, despite 1,179
local regression passes. A separate eight-case holdout passed; it did not cancel the omissions.
See `CONVERSATION_REVIEW.md` for the retained evidence and source hashes.

There is also an input-level conflict: the combined system instruction asks for facts, date
deferrals and questions, while its user-message wrapper says to extract only facts. This is a
plausible contributing cause, not an established causal explanation. A narrow question pass
changes both prompt and schema, so comparing only that pass to the existing combined call would
not show whether a simpler wrapper correction suffices.

| Arm | Change | Calls needed for a case |
|---|---|---|
| `baseline` | Existing combined prompt, schema and facts-only wrapper | 1 |
| `neutral_combined` | Same combined prompt/schema/provider settings; wrapper explicitly includes all three tasks | 1 |
| `focused` | Separate question-only prompt/schema; its questions replace only the baseline patch's questions | 2: baseline plus focused |

All arms retain the exact configured DeepSeek model, endpoint, temperature, token limit and
timeout. The order of the three measurement calls rotates by case. Every call sees identical
fictional email/context data, but the intended wrapper/prompt/schema differences are explicit.
The focused composition preserves baseline facts, date deferrals, ambiguities and review flags;
the normal source/confidence/conflict/workflow guards still run afterwards. It does not invent
a baseline patch if the baseline call fails. The neutral arm evaluates its own extracted facts.

## Frozen inputs and measurement rules

An independent author created `evals/question_understanding_cases.json` without reading code,
prompts, old corpora, reports or tests. The 36 fictional cases are split into 24 development
(12 Chinese, 12 English) and 12 reserved holdout (6 each). The original file SHA-256 is
`d479918a035b8f0bf0ee6d8c0040b9ae9afe9a182b67f0059190f7169c1d4c68`.
Expected profile corrections are evaluator-only and never supplied to the model.

Before measuring:

- Preserve every API error, missing question and content-check failure. No retries or output
  selection to obtain a passing sample; no overwriting reports.
- Check raw and guarded topic accuracy, false positives and omissions separately from schema
  validity, independent fact corrections, workflow/sender checks and actual reply usefulness.
- Inspect the replies, including automatically passing cases. A classification pass is not
  evidence of complete advice or natural tone.
- Compare single-call latency/usage against the focused two-call pipeline's combined latency/usage.
  Sharing a baseline during the experiment does not make the second production call free.
- Use old **development** cases as exposed regression inputs if needed, never as unseen proof.
  Freeze candidates before the first reserved holdout run; run that holdout once and retain failures.
- Do not promote an arm with a new unsafe fact/confirmation/release regression. If the single-call
  control provides comparable observed question coverage, prefer it over additional orchestration.
  Small samples do not establish universal accuracy or statistically proven equivalence.

The OpenAI Docs skill informed the explicit out-of-task alternative and separation of evaluation
objectives, datasets and metrics: [structured-output boundaries](https://developers.openai.com/api/docs/guides/structured-outputs#handling-user-generated-input)
and [evaluation design](https://developers.openai.com/api/docs/guides/evaluation-best-practices#design-your-eval-process).
These principles do not turn DeepSeek JSON mode into OpenAI strict-schema enforcement, and no
OpenAI model substitution is involved.

## Privacy and limits

Only fictional cases go to the model from this probe. Workflow/Gmail transport is captured locally
with network disabled during the simulated send. The adapter exposes the latest raw extraction
response in memory so this explicit evaluator can retain malformed fictional output; production
does not automatically log it or accumulate raw response history. API failures clear stale response
content before the next attempt. Raw retention is opt-in (`capture_raw_responses=True`) for this
fictional probe only; it defaults to disabled in the production adapter. Real recipient usability,
document processing and final delivery
are not measured by this experiment.

## Results retained without retries

All three reports used the same frozen source hashes and configured `deepseek-v4-flash`, with
180 provider calls and 410,568 reported tokens in total. Every extraction was schema-valid.
No current model prompt was tuned after the new holdout was opened; the holdout ran once.

| Corpus / evidence | Baseline guarded topics | Neutral guarded topics | Focused guarded topics | Selected full checks, baseline / neutral / focused |
|---|---:|---:|---:|---|
| New development, 24 cases | 22/24 | 24/24 | 24/24 | 21 / 23 / 23 |
| Previously exposed scope development, 24 cases | 22/24 | 24/24 | 23/24 | 22 / 24 / 23 |
| New reserved holdout, 12 cases, first run | 12/12 | 12/12 | 11/12 | 12 / 12 / 11 |

Reports are `eval_output/question_understanding_development_2026-09-04.json`,
`question_understanding_regression_2026-09-04.json` and
`question_understanding_holdout_2026-09-04.json`. Original failures remain unchanged.
All independently specified birthday/budget corrections reached their expected values in the
baseline and neutral arms. All captured workflows preserved confirmation/release restrictions;
this is not real Gmail delivery evidence.

The focused pass included an explicitly declined priority-services clause in its fee excerpt on
`scope_en_004`; the guard rejected it, despite the correct raw topic. It omitted an unrelated
classroom-writing request on the new holdout. Neither failure is repaired by relaxing the guard.
The neutral wrapper required no second call and had no observed question regression in these
samples. On new development, its mean extraction time was 0.943 seconds and its usage 68,462
tokens; baseline-plus-focused took 1.731 seconds sequentially and 96,052 tokens. These are one-run
observations, not a latency SLA, price quote or statistical proof of equivalence.

Decision: use the neutral wrapper as the default DeepSeek request. Its system prompt, schema,
model and provider settings are unchanged. `extract_case_patch_legacy_input` explicitly preserves
the old baseline for reproducible comparisons; the probe calls that method rather than silently
comparing the new default against itself. Contract tests compare full request arguments between
the promoted default and the measured neutral method. Raw-response capture stays off by default.
The architecture remains one model extraction plus deterministic validation/workflow/reviewed
answers, not a multi-agent chat system.

## Classification did not establish helpfulness

An independent engineering reader inspected all 72 development replies. Twenty-one of 24 cases
had identical replies across the three arms. Correct topics often still selected overly broad text:

- `qu_en_dev_006` asked how two accounts demonstrate accessible funds. All arms passed but mostly
  discussed statement months, without practical account-by-account records advice.
- `qu_en_dev_012` included “where I can obtain them” in every extraction. All arms correctly failed
  the acquisition-answer check; the renderer missed the pronoun-based subquestion.
- `qu_en_dev_010` only corrected a birthday and closed the turn. All arms passed yet appended an
  unsolicited application tutorial and student document advice.
- `qu_zh_dev_010` said not to continue for now, yet the date receipt offered to collect other details.
- `qu_zh_dev_007` baseline omitted the checklist classification but still produced a relevant list
  through existing fallback. That is a classification error, not a wholly unanswered email.

Content changes after the frozen measurement address the first four narrowly: practical financial
records and acquisition advice, period discussion only when requested, identity/contact corrections
not automatically triggering preparation guidance, conversational English birthday formatting and
a date-deferral receipt without the unsolicited “collect other details first” wording. The
[official financial-evidence section](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk#demonstrating-personal-circumstances)
was rechecked. Record organisation and bank-download suggestions are preparation advice, not new
mandatory evidence rules or acceptance guarantees. Six independent synthetic regressions were
red before these changes and green afterwards.

The acquisition-only regression no longer demands unrelated months boilerplate. The evaluation
checks period guidance when a period is asked and separately rejects invented fixed periods for
access questions. Old reports are not regraded in place. Any content replay uses the already
exposed development patches, records zero new model calls, and does not rerun the holdout.

Remaining: broader subquestion coverage, unsupported-route useful handoff, duplicate links,
overgeneral route disclaimers and actual recipient tone acceptance. Removing pushy date wording
is not a persistent pause/resume feature. No “full marks” or universal accuracy is claimed.

### Post-measurement deterministic evidence

`scripts/adviser_reply_replay.py` accepts an original completed three-arm development report and
its exact corpus. It verifies IDs, body, language, expectations and seeded profile, and uses only
the saved neutral raw patch. It rejects holdout and output overwrites; a saved extraction error is
unavailable, not an empty invented patch. No provider key or model is used.

The first new/old-development replays passed 23/24 and 22/24 respectively. They retained a real
regression (ordinary-topic deduplication dropped a second bank subquestion's context) and a
negative-scope oracle bug, not a new model failure. All first report bytes remain unchanged.
Both `*-v2.json` replays passed 24/24. Both final `*-v3.json` replays also passed 24/24 after a
readability follow-up; the latter include relevant current account context and avoid repeating
financial-record checks in the acquisition paragraph. These are exposed deterministic reply checks,
not additional model trials or unseen accuracy evidence. The final local suite passes 1,339 tests.

```bash
uv run python scripts/adviser_reply_replay.py \
  --source-report eval_output/question_understanding_development_2026-09-04.json \
  --corpus evals/question_understanding_cases.json --output NEW_REPLY_REPLAY.json
```

The registered Gmail worker was reloaded to PID 68387 with an observed completed idle cycle at
12:07:03 UTC. Full cases/outbox hashes remained equal (one case, nine SENT records), and the
LaunchAgent file was unchanged. The first bootstrap returned error 5; the verified subsequent
bootstrap succeeded. See `eval_output/gmail_question_wrapper_reload_2026-09-04.json`. This adds
deployment evidence, not a new ordinary-message reply or recipient naturalness result.
