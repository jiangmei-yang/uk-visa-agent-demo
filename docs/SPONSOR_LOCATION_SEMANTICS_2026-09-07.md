# Sponsor location is not proven residence

The source guard currently accepts both `is in the UK` and `lives in the UK`
(and Chinese equivalents) into `sponsor_is_in_uk`. A single boolean therefore
cannot establish residence. A negative residence answer also does not prove the
person is physically outside the UK. Existing receipts incorrectly strengthened
these inputs into `lives/does not live` or `住在/不住在`.

Normal and sponsor-name-correction receipts now acknowledge the customer's
location answer without asserting residence or its negation. A location-only
reply has a concise standalone acknowledgement rather than restarting a sponsor
introduction. The generic Chinese correction label no longer asserts residence.
Values, sources, extraction and policy gates are unchanged; no migration invents
which historical meaning was intended.

## Bounded validation

- The initial 14 cases exposed 11 failures; generic English corrections already
  lacked residence wording. The test was extended to cover both unknown and
  known sponsor relationships, exercising dedicated and generic correction paths.
- Sponsor location/ownership and related question/receipt tests passed 196 cases
  before the final standalone-location editorial adjustment.
- Automatic Gmail reply, editorial and progression integration tests passed 49
  cases in 1.60 seconds before that adjustment.
- After the adjustment, all 20 location receipt cases passed; full lint and
  configured typing passed. These checks include positive, negative and cleared
  values, both languages, and nonmutation of the case in ordinary receipts.

This is containment of an unsupported outbound assertion, not completion of the
underlying data model. Questions still use residence wording in several places,
while the extraction guard accepts both meanings. Presence, residence and legal
status need separate source-backed representation and gate review before claiming
full sponsor-location correctness. Rewording receipts alone does not establish
that a sponsor's identity/status evidence is complete.

No new live-provider experiment, full regression, CI or real Gmail acceptance
is claimed for this edit. Current source-bound provider checks still require a
fresh evidence checkpoint after the conversation changes. Historical reports and
the deployed worker remain unchanged.
