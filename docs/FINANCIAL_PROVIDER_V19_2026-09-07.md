# Financial provider v19

Four real DeepSeek extraction calls completed against the unchanged frozen
fictional PDF set at source `40d2b1ce8fd399917c1029b007997ba47ec8dd2d`.
All four passed; reported usage was 8,742 tokens, with zero mailbox calls and
zero real documents. The new report and receipt are v19; v18 remains unchanged.
The source binding contains 101 entries (all application Python files plus the
financial probe), computed from the actual manifest rather than copied from v18.

All four refreshed single-page previews were viewed, and all extracted financial
observations were compared with them: Kai Example's GBP 42,000 gross annual
salary; GBP 12,500.50 balance and GBP 13,000 corrected balance for account 1234;
Mina Example's separate HKD 88,000 sponsor balance for account 9876. The date is
2026-08-31; dates, periods, basis, subject and page/excerpt references were checked.
The source PDFs were not rewritten. These are uncomplicated fictional specimens,
not proof of general OCR or real-bank-document accuracy.

Strict current-source financial and consultant evidence tests passed 55 tests
in 1.99s. Lint and mypy (102 checked source files) passed. A separate complete
pytest run was started after the receipt/reference update; its outcome must be
recorded explicitly, not inferred from these focused checks. No Gmail state,
processing consent or delivery authorization changed.

The complete unfiltered run then finished: **5,376 passed, zero failures, one
Starlette deprecation warning in 107.34 seconds**. This includes the strict
current-source model-evidence checks and repaired sponsor/console tests; no
deselections or skips were used to obtain the result. It is local regression
evidence, not current remote CI, nontechnical installation or Gmail delivery.
