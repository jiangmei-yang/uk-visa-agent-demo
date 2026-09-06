# Separate source-bound sponsor location dimensions — foundation

`SponsorLocationStatement` now represents either `residence` or
`current_presence`, with its own boolean value, original event ID, literal
owner-bearing excerpt and caller-supplied sponsor identity. It is immutable and
rejects extra fields. Neither dimension implies legal status, future presence,
funding sufficiency or a complete sponsorship arrangement.

The bounded parser handles explicit singular `my sponsor` / `我的资助人`
statements, including a same-sentence contrast such as residence outside the UK
and current presence inside it. It retains both facts instead of selecting one.
Repeated contradictory values for the same dimension/identity remain available
for conflict detection. It does not resolve chronology or pick the newest winner.
Quoted, hypothetical, historical, uncertain, future and other-person statements
are not converted into facts. Unsupported clauses invalidate the whole sentence.

The Case schema can persist these statements through its existing JSON storage.
Deserializing an older case defaults to an empty statement list, never derives
either answer from `sponsor_is_in_uk`, and does not change that historical value.

## Verification

The focused parser/storage tests verify source retention, separate dimensions,
conflict preservation, exclusion cases, database reopen and old-boolean behavior.
Combined with automatic Gmail replies and birthday conversation regressions:
**60 passed in 0.85 seconds**. Configured typing passed for 96 source modules;
the initial missing result-list annotation was fixed. The curly-apostrophe
contraction is covered separately so it is not mistaken for a quotation.

## Not yet connected

This is a data/parser foundation, not a completed behavioral fix. The automatic
workflow does not yet append these statements or ask dimension-specific questions;
material applicability still uses the legacy boolean. The next integration must
enforce consent/event order, bind observations to the current sponsor, preserve
history across sponsor replacement, resolve conflicts explicitly, invalidate stale
confirmations, and prevent an unresolved update from silently preserving an old
waiver. Planner, summaries and final gates need corresponding tests. Do not claim
that ordinary Gmail now handles these mixed statements correctly.

No full-suite, new live-model/CI, migration of real cases, or Gmail deployment was
performed here. Earlier source-bound reports remain historical for this new module
and Case-schema change; their freshness checks are not weakened.
