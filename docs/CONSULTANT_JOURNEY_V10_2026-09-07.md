# Consultant journey v10: fresh extraction, not perfect dialogue

Actual source `66b35f964834be217e36294e05891298e9efb001` was exercised by
`scripts/consultant_journey_probe.py --scenario-set all --allow-model-calls`.
The retained report is `eval_output/consultant_journey_2026-09-07-v10.json`.
22 extraction calls completed with 22 passing existing checks, 124,430 reported
tokens, zero retries, zero mailbox calls and no real documents. All 22 replies
were read. The strict source-bound current-report regression now references v10;
the complete module passed 46 tests in 1.96s. Earlier reports are unchanged.

This is the same exposed four-journey development set, not a blind evaluation.
Rendering is deterministic, not model-generated consultant prose. One substantive
editorial defect remains despite passing checks: self-employed-host-and-funds
turn 3 asks what to use instead of payslips. The actual model classified this as
unsupported. The response therefore opens with a generic lack-of-guidance line,
then provides relevant self-employment document advice. The valid advice does
not excuse that contradictory opening. It needs a targeted full-reply regression
and a routing fix; no naturalness score is inferred from 22/22.

Separately, the original seven-turn sponsor journey was replayed without network
into `eval_output/sponsor_location_replay_2026-09-07-v1.json`. Only turns 4 and 7
passed its historical checks. The changed location dimension/question order no
longer matches its legacy boolean/address expectations. Reading all seven replies
also exposed a genuine residence-preface contradiction; that was fixed and
tested before the v10 run. The failed replay remains intact. It must not be
advertised as a current passing sponsor journey or silently rewritten.

Financial provider freshness, complete sponsor dialogue, reviewer UX, rendered
final summaries, full regression/CI and real authorized Gmail recipient delivery
remain outstanding. No live Gmail worker was restarted or applicant approved.
