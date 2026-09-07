# Customer email copy repair and live reload

The reported email exposed a long bilingual privacy contract, model/version labels
and an internal-thread confirmation phrase. Notices now use the language of the
current reply, falling back to the case language. Model/version labels and the
duplicated English/Chinese block are absent. Grant and withdrawal receipts use
the same single-language approach. The necessary provider, AI processing, local
retention, earlier-message scope, withdrawal/deletion limitations and non-training
uncertainty remain disclosed; the change does not conceal third-party processing.

`ProcessingScope.notice` remains the stable internal scope contract for existing
authorization fingerprints. Customer-facing text is compiled separately and stored
as the exact canonical outbound payload. Provider/model changes still invalidate
consent; presentation changes neither grant it nor erase its existing audit trail.
The current sent reference and original-message linkage remain mandatory. Tests
now find the current notice by its reference and canonical payload, not by requiring
an internal model identifier to be displayed to the customer.

Validation: 258 targeted consent/Gmail tests passed in 9.11s. After the final
customer-phrase cleanup, all six copy tests passed, including parsing the actual
displayed agreement sentence. Full lint and typing (103 source files) passed.
A full unfiltered regression was started separately; do not infer its outcome from
these targeted checks.

Live operation: stopped the exact existing `com.visa-agent.gmail-user` service,
verified old PID 69511 exited, backed up both private SQLite databases (both
integrity checks `ok`), then bootstrapped the unchanged plist. New PID 4230 was
observed running, with a completed idle cycle and no dispatch. Bidirectional SQL
EXCEPT comparisons of cases, outbox and processing consent against the backup
returned zero differences in all six comparisons. Counts remain one case, nine
processed events, ten outbox records and zero deliveries. No consent was granted,
no historical mail resent, and no final pack sent. Already received emails cannot
be recalled by changing local code.

Unrelated, interrupted sponsor-pack QA work is preserved separately in the
working tree; it is not claimed as completed by this email-copy repair.
