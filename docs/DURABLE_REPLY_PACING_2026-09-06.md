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

The first live pacing run v4 reported 22/22 under its then-current checks. Manual
reading rejected full acceptance: English pacing turn 4 had an empty model patch
and asked about accommodation instead of offering the requested practical action.
The report remains unchanged. Contract v5 requires a concrete intended-itinerary
action and no intake question for that turn. The repaired fallback selects only
reviewed advice for an established current case, preserves separately answered
questions and rejects other applicants/routes and hypothetical scopes.

The next live run v5 correctly retained **21/22** rather than being retried to
green: the model omitted the English application-form question on pacing turn 3.
The form-entry fallback now uses the already established visitor context only
for a complete plain form-entry question (open/find/access/fill in/fill out).
Other routes, other applicants, mortgage applications and qualified questions
cannot borrow that context; the link remains conditional guidance, not a route
confirmation. Raw provider reports v4 and v5 are retained for offline replay.

## Fresh provider results and manual reading

The final v6 run completed **22/22** with contract `consultant-journey-v5`, zero
retries, no Gmail calls, and source revision `b67b2006893203d942ffc7e6c3f4881cc655db5c`.
Its report SHA-256 is `c2f8caa48e86aac0e03d037f832888cc75f7c6268166d921008f15038f15de23`.
All 22 actual captured replies were read, not just their checks. The two omitted
English questions now receive the official application entry and one intended-
itinerary action respectively. Undecided dates are not requested again; identity
updates survive reopening; sponsor changes and a preparation pause remain scoped.

This manual review is by the implementer, not an independent naturalness score.
Opening acknowledgements and school-record explanations remain fairly formal;
brief mode deliberately does not truncate the reviewed evidence limitations.
These known stylistic limits and broader unseen paraphrases remain outside the
bounded pass claim. No "human-perfect" result is asserted.

The three retained journey runs v4–v6 used 66 calls and 245,489 provider-reported
tokens (236,913 input / 8,576 output). v4's weak green and v5's failure were not
overwritten. The matching financial v13–v15 runs used 12 calls and 26,214 tokens;
each extracted four frozen fictional PDFs. Current v15 binds the probe plus all
80 source files and passes 4/4, SHA-256
`6c20dcf6de248fb67cc13bc502cd58bc5a85a88a0705e335332c8918924d0ad5`.
The four rendered PDF pages were visually checked; labels and content were
readable and no PDF was modified.

The isolated fresh-container HTTP run passes 12 checks, including correction,
confirmation, duplicate-click protection and ZIP delivery. Its downloadable ZIP
SHA-256 is `69508420e47d5f843bf89eeb088347fa1d0e1cb2252d91c8108fb98bbdb18582`.
See [the retained HTTP report](../eval_output/pacing_release_smoke_2026-09-06.json).

## Local regression

Full suite, with both current source-binding checks enabled: **4,587 passed**,
one existing FastAPI/Starlette test-client deprecation warning, 96.66 seconds.
Ruff, strict Mypy (80 source files) and `git diff --check` passed. Saved provider
proposals v1–v6 are replayed through the current workflow without network access;
tests also cover route/owner/conditional boundaries and SQLite reopen/isolation.

## Local deployment preservation

The deployed loopback console is healthy, container
`4b15d3cc7e1b83ba95daeba479999c480061b96798ed6eb37ad6b096977a482c`, image
`sha256:f3038578536823fa9e979b10aa23962b89b0dc5874ae5e9791c1e83a764387f6`.
All 80 deployed source files match local source; the sorted path/hash JSON digest
is `5a940aa841701be349d272990a5bc4742b219f42347fa00524cbc7154050c846`.
The named runtime volume was preserved, as was the existing ZIP hash
`8bc0681a837437d30675da7650cd9c61e1ecc69532ea969c77ceb383c458f724`.

The existing Gmail worker was restarted with unchanged configuration, PID 56119,
observed idle at `2026-09-06T05:51:40.111483+00:00`. Its database dump digest was
identical before and after: `e50176d0c1de0e690a1f37a1c81ad9d56b45eb01241fe359a74bc28bfadec900`.
The retained state remains one case, nine processed events, nine SENT rows and
zero deliveries. No test email was sent and no applicant consent was fabricated.
These observations prove preservation and startup, not new recipient-side Gmail
delivery or continuous availability after the local computer stops.
