# Remaining end-to-end acceptance, 2026-09-04

Overall acceptance remains incomplete. The original supplied design is a
requirements reference; recommendations about vendors or libraries are not
themselves proof of product completion. Automated pass counts are not a substitute
for these outcomes.

## Technical work still required

1. **Applicant processing consent before ordinary personal-material processing.**
   A versioned notice/provider-scope/grant/revocation ledger and Gmail preflight
   have now been implemented and exercised with isolated, captured transports;
   see [the exact boundary and remaining limits](PROCESSING_CONSENT.md).
   `CONSENTED` is a workflow-stage label, not proof that consent was obtained.
   WhatsApp pre-download consent, mixed consent-plus-material emails, independent
   notice usability and a real applicant grant still require verification.
   OAuth, a mailbox allowlist, operator approval, or the retry CLI's model
   flag is not applicant privacy consent. Do not ask a new participant to send
   ordinary personal documents until this is addressed and consent established.
2. **Ordinary-document cross-source evidence coverage.** The natural PDF proposal
   schema currently extracts a bounded set of identity, invitation dates,
   occupation, funding category and translation-link facts. It does not establish
   comprehensive income, sponsor identity/relationship or financial-amount
   cross-checks. Generic conflict logic cannot compare facts the reader never
   extracts. Explicitly synthetic fixture success is not proof of this coverage.
3. **Requested intake coverage.** Ordinary travel history and UK contacts are not
   structured, provenance-backed profile fields. A serious-history flag is not
   equivalent to complete travel history.
4. **Independent usability and real transport recovery.** Native Windows setup,
   uncoached interviewer use and real provider failure windows are not established
   by Linux/container or captured-transport tests.
5. **Consultant usefulness beyond canned scenarios.** The [seven-item independent
   review](ADVISER_USEFULNESS_REVIEW_2026-09-04.md) records ordinary questions that
   a linked response or one-question limit did not solve. Specific-document help,
   exploratory consultation and foreign-work context now have local repairs.
   [Delivery-backed FAQ continuation and two preparation obstacles](ADVICE_CONTINUATION.md)
   now have bounded local/captured-sender repairs, including mixed facts/files.
   Broader paraphrases, other unavailable evidence and bounded consultation-context
   retention still need work. No new live model run or independent recipient trial is implied.

## Bounded recovery added in this work

- Gmail metadata HTTP 404 observations now retain the candidate and an audit count.
  Other candidates may enter the case, including pauses/corrections, but automatic
  sending remains held until discovery is drained. This is not a skip/delete
  action, proof of permanent deletion, or a complete disposition for permanently
  unavailable messages. Restored old messages may still need chronological review.
  The distinction follows Google's [error guidance](https://developers.google.com/workspace/gmail/api/guides/handle-errors).
- A signed Twilio inbound message must also match the configured account SID and
  service recipient before downloading media or creating queued work. This is a
  locally tested isolation boundary, not evidence of a live WhatsApp device trial.
- `scripts/review_document.py inspect/retry/replace` offers explicit audited local
  recovery for unknown/unreadable PDFs. It retains source hashes and read attempts;
  only the selected technical-failure chain can be superseded. Known identity
  warnings and unrelated blockers cannot be manually accepted with this command.
  Source bytes, applicant events and held updates are not fabricated or edited.
  It invalidates old confirmations/drafts and never sends mail or generates a ZIP.

Inspect an existing local case before choosing a recovery operation:

```sh
.venv/bin/python scripts/review_document.py inspect \
  --state-dir data/REGISTERED_STATE --case CASE_ID
```

Recovery additionally requires a selected document, the freshly inspected case
fingerprint, an operator name and a substantive reason. `replace` requires an
already received, normally classified replacement. `retry` requires
`--allow-model-processing` and invokes the configured normal reader; it can incur
API charges and **does not establish missing applicant consent**. Operator identity
is locally asserted, not an authenticated multi-user permission system.

## External outcomes still required

A consented ordinary-material journey through actual recipient-side final ZIP
delivery, plus an independent participant who is not coached with test markers,
is still outstanding. WhatsApp account/device acceptance remains deferred; the
adapter contract is not a claim that the live service is configured. No automated
test count or isolated recovery result closes these requirements.
