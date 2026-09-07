# Fresh seven-turn sponsor journey

The scenario's customer messages were preserved. Assertions now require separate
current sponsor residence sources, unknown physical presence, and a closed
applicability release gate instead of the old conflated boolean. Address source,
deferral, replacement, correction and one-main-question checks remain.

The first new provider run, v4, failed five turns: the model omitted the clearly
stated UK holiday purpose, causing repeated purpose questions and breaking later
address context. That report is retained. A bounded recovery now recognizes an
explicit first-person holiday statement only when purpose is otherwise missing,
extraction did not fail and no ambiguity/review exists. It still passes the
ordinary ownership/source/value guards, cannot overwrite known purpose, and
cannot grant route approval or applicant confirmation. Twelve regression tests
include the actual omitted proposal and quoted/conditional/negated/other-person/
mixed-purpose rejection.

Fresh v5 ran after the repair at source `0eb4d5e`: **7/7 passed**, seven real
DeepSeek calls, **39,671 reported tokens**, no retries and no mailbox calls.
Every captured reply was read. Address deferral survives; replacement asks for
the new person's address; the short new address and later Chinese correction
are retained; unknown current presence is not fabricated. The first reply is
still lengthy and one follow-up uses a generic greeting; passing this exposed
development journey does not prove perfect naturalness or blind robustness.

The saved-proposal regression now uses v5 and additionally asserts current
residence source and non-waiver checks. It passed with the holiday recovery and
address workflow tests: 30 passed in 1.04s. Lint and mypy (102 source files)
passed. v3/v4 and failed replays remain unchanged. No real Gmail case was touched.
The general consultant/financial freshness checks, full-suite/CI and actual final
Gmail recipient delivery remain open.
