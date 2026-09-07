# Respect an explicit request for only the application entry

The retained consultant v12 student journey requested one official entry but got
the full application sequence. Application answers now omit the sequence only
when the current customer explicitly requests just the entry/link. Conditional
route wording and the reviewed official source remain. Requests for detailed
steps still receive the sequence; separate questions are not discarded.

Tests also exposed Chinese `只要官方链接` being mistaken for a conditional
(`provided that`). Only a standalone, exact link-preference clause receives the
exception. Quotes, negation, hypothetical statements, requests about another
person and larger clauses containing conditions/approval guarantees retain the
original scope boundaries. This preference changes neither case facts nor consent.

Verification:

- 364 related question/scope/preference/cold-start tests passed in 1.81s.
- Final 20 dedicated tests passed in 0.15s, including preservation of a separate
  fee question. This set overlaps the preceding regression run.
- Full lint and typing (102 source files) passed before the final test addition.
- All 22 original v12 provider proposals were replayed, with unchanged customer
  messages, in `consultant_entry_brevity_replay_2026-09-07-v1.json`: all passed,
  zero new model or mailbox calls. The changed student turn 5 and the unchanged
  Chinese/English detail-request turns were read in full. Replay binds source
  hashes from this modified working tree; its base HEAD is 6d756f6.

This is not new live-provider evidence. The previous full-source provider reports
now predate this implementation edit, so their strict freshness checks must not
be claimed current until refreshed. The remote CI for 6d756f6 is a separate
pre-edit candidate. No Gmail worker deployment, email send or live consent update
was performed here.
