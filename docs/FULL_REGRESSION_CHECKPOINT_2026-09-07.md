# Unfiltered regression and live-service checkpoint

At source `7245cee`, the complete pytest suite ran without exclusions:
**5,357 passed, 7 failed, 1 Starlette deprecation warning, 105.46 seconds**.

Failures were inspected, not silently dropped:

- Consultant v10 and financial v18 source-freshness checks correctly reject
  source changes after those provider runs. New real runs remain required.
- Three sponsor-address workflow cases failed because the new location question
  took precedence over identifying the new sponsor's address. The planner now
  retains address-first collection when typed location observations exist. This
  keeps source-bound short address answers and deferrals intact without waiving
  the independent location applicability gate. The combined address/location
  workflow regression then passed 67 tests in 1.44 seconds.
- The old seven-turn sponsor replay still expects the legacy conflated boolean;
  it remains unresolved as a current release test. Its evidence is preserved.
- The console expected 15 checks. The added independent human-review check makes
  16; the assertion was updated explicitly. That console/download test passed.

Lint and mypy (101 source files) passed after these repairs. A second full-suite
green run has NOT been claimed; targeted fixes are not equivalent to that run.

Read-only live checks found LaunchAgent `com.visa-agent.gmail-user` running with
PID 69511, confirmed by `ps`. Its counters remained 1 case, 9 SENT messages,
processing consent `unknown`, and 0 deliveries. Worker log reported no automatic
dispatch. The process was not restarted, authorized or modified; it still runs
the previously loaded version, not these new repairs. Applicant processing grant
and real recipient-side final ZIP verification remain outstanding.

Local Git remote-tracking refs (not a fresh remote fetch) still showed candidate
`b3c391a` and main `735f5ea`; latest local repairs are not yet published as a
verified release. Do not present the historical CI as covering current source.
