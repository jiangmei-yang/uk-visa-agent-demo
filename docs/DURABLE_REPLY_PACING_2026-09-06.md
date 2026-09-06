# Durable consultant pacing, 2026-09-06

Work continues under an explicit active goal rather than treating one green test
batch as completion. This change addresses the known pacing issues left by the
preceding journey repair; it is not a universal naturalness or accuracy claim.

## Communication preference, not application authority

`Case.reply_style` defaults to `standard` for old cases. An explicit current brief
request saves `brief` with the originating event and exact customer excerpt.
It survives SQLite reopen and belongs to that case alone. A one-reply instruction
("这次请详细解释" / "For this reply, please explain in detail") overrides display
for that reply without replacing the saved preference. An explicit reset restores
standard pacing. Conflicts, quoted/reported, conditional, future-only and translation
requests do not update it.

This state is not a `CaseProfile` fact. It does not affect the profile/document
summary fingerprint, requirements, consent, preparation pause, confirmation or
release gates. A style-only instruction receives a short acknowledgement, not a
new intake question. A mixed substantive request is still answered normally;
reviewed legal answers are not blindly truncated to meet a character count.

## One next action

When a customer explicitly asks for one practical action, the adviser does not
combine the student letter with bank records or append a separate intake question.
The unoffered financial guidance is not marked SENT or complete; later preparation
can still address it. Generic "下一步" is deliberately different from "只告诉我一件事".
A regression initially caught that accidental overlap, and the original broader
process-answer expectation was restored without weakening its test.

Brief replies omit an unsolicited process overview and repetitive administrative
introductions. If the current message explicitly identifies a parent payer, the
brief identity question asks for that parent's name without another paragraph
repeating the known relationship. It does not silently persist an extracted
relationship or waive relationship evidence.

## Validation contract

The probe retains the original 12 consultant turns and adds 10 new Chinese/English
pacing turns. The added journeys cover persistence after identity updates,
temporary detailed explanations with an official application answer, subsequent
return to brief pacing, and explicit preference reset. Every reply is captured by
the real reviewed sender/outbox path with SQLite reopening; no Gmail call is made.

```sh
.venv/bin/pytest tests/integration/test_durable_reply_pacing.py
.venv/bin/python scripts/consultant_journey_probe.py --scenario-set all --allow-model-calls --output eval_output/new-pacing-run.json
```

The paid command is 22 fictional model extractions, with no retries; an existing
report path is refused. Failed runs must be retained. Source-bound financial
extraction and full regression are refreshed after the implementation is stable.

The preceding fully bound journey/financial reports remain historical evidence;
their file hashes must not be rewritten to make this new source appear tested.
