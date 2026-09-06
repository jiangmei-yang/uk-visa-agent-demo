# Sponsor-location semantics: source and rule audit

Official source inspected September 7, 2026:
[GOV.UK supporting-document guide, sponsor section](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk#3-if-you-have-a-sponsor).
It asks for evidence of the sponsor's lawful UK status where applicable. It also
covers the support, how it is provided, funding and relationship. It does not
equate that applicability with permanent residence alone.

## Code and observed behavior

`build_requirements` adds `sponsor_uk_status` only when `sponsor_is_in_uk is True`.
The source guard accepts both presence and residence language into that boolean.
`scripts/sponsor_location_audit.py` is a fictional, read-only reproducible probe:
no network, databases, actual documents or final gate execution. It constructs
an already-known personal sponsor and a requested location field.

The controls verified that `My sponsor is in the UK.` accepts true, while
`My sponsor does not live in the UK.` accepts false; opposite proposals are
discarded. Four mixed English/Chinese statements about residence versus current
presence, each tested with both proposed booleans, yielded no accepted update,
no ambiguity and no human-review flag. The same mixed statements without known
profile context also yielded no update; the known-profile run rules out simply
omitting sponsor context as the explanation for the observed outcome.

Discarding such a statement is not proof of correct understanding. In an existing
case it raises a stale-fact risk because the prior boolean may still govern the
requirements. This probe does **not** demonstrate an actual final ZIP bypass:
other facts, evidence and confirmation gates have not been exercised here.

## Required implementation work

Represent residence and current/proposed presence separately, with the original
source and sponsor identity. Unknown historical booleans must not be silently
backfilled into either fact. Add source-bound clarification for mixed statements,
invalidate affected confirmations when the situation changes, and decide the
status-evidence applicability explicitly instead of assuming that a discarded
update leaves the old answer trustworthy. Cover false-to-unknown and replacement
sponsor transitions through the actual workflow and final gate.

The previous receipt containment remains useful but does not close these data
and rule requirements. No policy YAML, extraction model, gate or Gmail service
was changed by this audit. The new diagnostic script passes repository lint;
it reports observations rather than labelling the unresolved behavior a pass.
