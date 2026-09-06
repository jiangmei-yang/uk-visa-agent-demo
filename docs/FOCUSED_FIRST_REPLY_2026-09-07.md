# Focused first preparation reply: development checkpoint

The real residence experiment exposed a long first response that combined one
case-specific preparation action with unsolicited fees, timing, biometrics and
another booking caution. This is a reply-composition issue; the model had not
invented an application question in that turn.

The proactive application orientation now provides a short official route/form
entry and the distinction between preparing information and submitting it. It
does not append unasked prices, processing estimates, appointment instructions
or a second booking reminder. Existing guidance topic IDs remain unchanged, so
previously SENT advice is not resent merely because its editorial text changed.
Explicit question answering is separate and unchanged.

Replaying the same real v2 residence proposals on the changed workflow passed
**4/4** (`eval_output/residence_duration_2026-09-07-v2-replay-focused-opening.json`).
The initial reply fell from **276 to 195 whitespace-delimited words**, excluding
URLs, compared with the earlier receipt replay. The itinerary action, original
duration, date deferral, official sources and one passport question remain. This
count is a readability proxy, not a naturalness score or evidence of perfection.

An actual captured-workflow regression checks the initial absence of unasked
fees/timing and duplicate booking advice, then sends an explicit compound request
for fees, timing and the online application. The latter must still answer all
three without adding intake questions. The guidance/reopened-workflow group
passed **156 tests** (0.88s). Ruff and strict Mypy passed for 92 modules.

The development-wide regression passed **4,954 tests**, with **2 deselected**
historical source-bound provider reports and one Starlette deprecation warning
(101.67s). Those two old reports still need fresh source-bound provider runs;
their exclusion is not a release acceptance result.

No new model calls, real mail, service changes or user approvals occurred. The
saved live v2 report is historical; this is explicitly an offline replay with
current code/source hashes. The first response can still benefit from further
editing and independent user feedback. Full-requirement intake, human-review UI,
fresh model/Gmail/container acceptance and GitHub delivery are not closed by this
copy change.
