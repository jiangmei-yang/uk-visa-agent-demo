# Conflicting employer statements cannot be resolved by model omission

The existing guard rejected two conflicting model-proposed updates. It did not
detect two conflicting current statements when the model returned only one (or
neither). The literal completion parser excluded conflicting values, but omission
alone did not trigger review. That left a route for silently selecting one value.

The bounded current-owner parser now exposes source-level field conflicts. The
outer guard checks the latest unquoted reply independently of proposed updates.
Conflicting name statements remove all proposed employer details, because the
contact owner is unresolved. Phone/address conflicts remove the affected field.
The case receives an explicit ambiguity and the existing human-review hold.
Prior facts are not silently replaced; this does not invent a new employer,
approve a document or satisfy a confirmation gate.

Tests cover English/Chinese names, empty/first-only/last-only model proposals,
contacts withheld for unresolved names, conflicting phone/address statements,
and negative controls for repetition, historical/conditional/other-person facts.
A network-forbidden workflow test checks that the hold and reason persist through
SQLite reopen, with no employer evidence, customer confirmation or generated ZIP.

Focused pre-existing employer/evidence/replay group plus initial conflict tests:
50 passed. Added contact-conflict and persisted-workflow group: 13 passed. Ruff
and strict Mypy passed (93 modules). These are deterministic fictional tests,
not new live-model or Gmail results.

Full development suite (collected before the final two contact tests and the
persisted-workflow test): **5,081 passed / 2 deselected**, 103.21 seconds, one
existing Starlette warning. Those three additional tests passed in the focused
13-test run above. The same two stale source-bound provider reports remain
excluded (`test_current_journey_report_binds_all_source_and_probe` and
`test_current_provider_run_is_bound_to_complete_source_prompt_schema_and_pdf_set`);
their current-source release acceptance has not been claimed.

## Remaining limitations

This is a narrow explicit-statement conflict guard, not general language conflict
resolution. Multiple genuine employers, alternative contact numbers, and a
same-message old/new correction may require clarification rather than automatic
selection. The existing human-review path is used; this checkpoint does not
claim a new conversational clarification-and-resolution flow. That customer
experience, broader wording, source-bound document review and final release
acceptance remain open. No deployment, real mail or operator approval occurred.
