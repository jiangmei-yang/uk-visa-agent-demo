# Employer intake: real-provider failure and repair evidence

This is a bounded preparation-workflow checkpoint, not release acceptance or a
naturalness score. All identities are fictional; delivery is captured locally.
No Gmail message, operator approval, deployment or GitHub push was performed.

## Immutable experiments

| Report in `eval_output/` | Model calls | Tokens | Contract checks |
| --- | ---: | ---: | --- |
| `employer_intake_2026-09-07-v1.json` | 9 | 50,935 | 4/9 |
| `employer_intake_2026-09-07-v2.json` | 9 | 50,981 | 9/9 |

Total: 18 real DeepSeek calls, 101,916 tokens. These reports retain the source
hashes and outputs of their actual runs. V2 predates the final independent-scope
review-signal guard change; it must not be described as a live run of that change.
`employer_intake_2026-09-07-v2-replay-review-scope.json` separately passes all nine
turns through current code using saved raw proposals, with zero API/mail calls.
The earlier `v1-replay-literal` report remains historical evidence, not overwritten.

## What failed and changed

- The provider omitted a short address despite a verified, actually sent question.
  Bounded literal completion now supplements missing model fields only where the
  original text and existing ownership guard establish the exact value. It does
  not overwrite a model field or run during fallback, ambiguity or review hold.
  Evidence explicitly records `bounded_literal_employer_parser`, not model extraction.
- An unrelated next-step question incorrectly invalidated an independent current
  employer statement. Question punctuation is now checked per statement.
- A Chinese correction prefix omitted from the model excerpt caused rejection
  even though the owner/value clause was retained. The owner clause remains
  required and the full statement's framing is checked independently.
- Explicit model review signals on own-employer statements are now retained;
  these statements must not be discarded as entirely about another person.

Tests cover source events, persisted state, short-answer question context,
unknown phone deferral, company changes clearing old contacts, and subsequent
phone corrections. Literal evidence remains unverified: no customer confirmation
or pack-release authority is acquired through parsing.

## Editorial review: not yet satisfactory

V2's actual replies still restart with “Hello” and generic reassurance mid-thread,
show “Employer Name” in a correction receipt, and fail to acknowledge the precise
new phone detail in one turn. The home-address explanation is duplicated. The
functional 9/9 result explicitly does not establish human-like conversation.
These are concrete follow-up editorial defects, not passed acceptance items.

Broader grammar, conflicting literal statements, unnamed/multiple job changes,
employment-letter identity review, current-source full provider/PDF/container
acceptance and real recipient-side Gmail finalpack verification remain open.

## Development verification

Focused domain/workflow checks: 39 passed. Network-forbidden saved-proposal
regression: 1 passed. Full development suite: **5,060 passed / 2 deselected**,
102.34 seconds, one existing Starlette deprecation warning. Ruff and strict Mypy
passed (93 modules). The two excluded tests are precisely:

- `test_current_journey_report_binds_all_source_and_probe`
- `test_current_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set`

Those stale live-provider source bindings remain outstanding release requirements.
They were not rewritten to match new hashes or counted as passes.
