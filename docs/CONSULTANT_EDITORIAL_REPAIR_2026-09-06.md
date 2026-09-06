# Consultant wording and upgrade continuity

The continuing acceptance goal remains active. This is one repair within it,
not completion of the wider acceptance audit or a perfect-agent score.

## Changes

- A brief, proactive first step confirms passport/application location without
  reciting the whole intake form. The full facts and excerpts remain in the case.
- In other replies, reviewed guidance can acknowledge the occupation, purpose or
  funding itself; the receipt does not repeat that sentence. Explicit corrections
  and unselected guidance retain the original acknowledgement behavior.
- School-record help starts with the available record, the checks to make and
  how to resolve missing details. It retains the original-file and sufficiency
  limitations without several overlapping warnings.
- Existing school discussion memory depended on exact sent wording. Both the
  retained old wording and current wording are recognised, but only with the
  matching case/event and a full SENT payload with provider ID and timestamp.
  Old records are not rewritten or upgraded into evidence acceptance.

The GOV.UK [supporting-document guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
was checked on 2026-09-06 (page updated 2026-02-25). It lists an education-provider
letter as an example; the record checks here are preparation suggestions, not an
official fixed-field checklist or a promise that a portal export substitutes for
a letter. No new visa eligibility or document sufficiency rule was introduced.

## Development evidence and failures

The initial editorial offline replay retained in
`eval_output/consultant_editorial_offline_2026-09-06.json` passed 22 checks but
manual reading found the first receipt was still a long recital. The subsequent
brief-context rule therefore compresses it further; that old replay is historical,
not evidence bound to the later source.

An interim full regression had 4,606 passes, five failures and two deliberately
excluded stale-source binding checks (97.08s). All five failures required exact
old receipt wording even when the advice itself already acknowledged the fact.
Those expectations now inspect the equivalent reviewed advice and additionally
assert persisted occupation, purpose and funding. No original provider report
was modified to green. The focused consultant set then passed 144 tests.

Upgrade tests dispatch old wording through the isolated real outbox sender,
reopen SQLite and resume under current wording. They also reject partial,
unsent, wrong-customer and wrong-event history. Initial setup failures exposed a
test simulation that patched the old renderer but not its imported workflow
binding; both old bindings are now simulated, without fabricating SENT flags.

Final provider/source-binding, full regression and rollout evidence will follow
the code freeze. The existing Gmail service has not yet been changed by this
editorial repair.
