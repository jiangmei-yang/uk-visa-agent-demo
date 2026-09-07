# Structured sponsor-location export

`05_application_answers.json` now receives a versioned `sponsor_location`
section when separate observations exist for a personal-sponsor case. Residence
and current presence each have `state` (`reported`, `unknown`, `conflicting`),
`value` (boolean only when unambiguous), and current-identity/current-epoch
sources. Contextual short replies retain their question-event reference.

The basis is explicitly applicant-reported information, not legal-status
verification. Operator identity/review decisions are not exported as customer
facts. Historical observations of a replaced sponsor are not attached to the
current person's dimensions. In non-personal funding, no new sponsor section is
added. Where typed history exists, competing legacy profile/evidence assertions
are removed from the export only; persisted history is not modified. Legacy
cases without typed observations are not silently converted.

23 export and summary tests passed in 0.17s, covering complete, unknown,
conflicting, replaced and self-funded states, preserved unrelated facts, JSON
serialization and no mutation of case state. Lint and mypy (101 source files)
passed. The production pack writer calls the tested export helper.

This turn did not generate or visually inspect a complete new ZIP/PDF pack.
That end-to-end artifact validation, fresh provider evidence/full regression,
and actual authorized Gmail final delivery remain outstanding. No real customer
state, message or processing consent was changed.
