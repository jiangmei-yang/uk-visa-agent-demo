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

## Fresh source-bound evidence

After source freeze at `8e84197bb8e4307553269239c169643fcf3dda16`, journey v7
passed 22/22 with zero retries. All 22 actual captured replies were read. The
brief first receipt now confirms two contextual facts and moves to the next
action; the school-record response retains concrete checks and its limitation.
The report SHA-256 is `a8d25e2be3001ca5c4564f719d5a637ec92e25bb9a55938fdf8a91751595316b`.
Provider use: 78,966 input / 2,667 output / 81,633 total tokens.

Financial v16 passed 4/4, with all 80 source files plus its probe bound to the
report. SHA-256: `71a80b2b8c472d1bc34a4495f0c8372c3c408c487aa656ca9dd55850659216e8`.
Provider use: 7,485 input / 1,259 output / 8,744 total tokens. These are fictional,
exposed development cases, with no mailbox calls or real applicant documents.
The full goal still requires unseen language and independent usability evidence.

Fresh-container HTTP checks pass 12/12, including correction and confirmation
gates and the final pack; see
[`editorial_release_smoke_2026-09-06.json`](../eval_output/editorial_release_smoke_2026-09-06.json).
The ZIP hash remains `69508420e47d5f843bf89eeb088347fa1d0e1cb2252d91c8108fb98bbdb18582`.

Full local regression with both current provider/source-binding checks enabled:
**4,617 passed**, one existing test-client deprecation warning, 96.73 seconds.
Ruff, strict Mypy (80 source files) and diff whitespace checks passed. Historical
provider proposals through v7 are covered by offline regression, not overwritten.

## Deployment preservation

The loopback console is healthy after deployment, container
`7aeb2c0316291493a9324ee9bee1a3a273d43f82d567dea3ee9339a48a38d3b5`, image
`sha256:e2a730ba714c14ce395d2e40d89058b755c1ff17ae32a89159212970e8638231`.
All 80 deployed source files match local source (sorted path/hash JSON digest
`4c7102924906e1962255fc17844254b85fa9d585ad6d4c137f032f510e695468`).
The named runtime volume and the existing ZIP SHA-256
`8bc0681a837437d30675da7650cd9c61e1ecc69532ea969c77ceb383c458f724` were preserved.

The unchanged Gmail worker configuration was restarted, PID 61377, observed idle
at `2026-09-06T07:02:22.974949+00:00`. Its before/after database dump SHA-256 is
`e50176d0c1de0e690a1f37a1c81ad9d56b45eb01241fe359a74bc28bfadec900`:
one case, nine processed events, nine SENT rows, zero deliveries. No new email
was sent and no applicant processing consent was fabricated. This is startup and
preservation evidence, not a new recipient-side transport acceptance.
