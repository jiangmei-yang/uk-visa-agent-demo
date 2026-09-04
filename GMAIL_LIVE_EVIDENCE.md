# Gmail live sandbox evidence — 2026-09-04

Status: **partial live acceptance; not production-ready**. Updated after ordinary-language testing.

The experiment used a dedicated service mailbox, an owner-controlled sender mailbox,
and a clearly labelled fictional tourism enquiry. Addresses, tokens, provider IDs,
and raw messages are deliberately excluded from this report.

## Observed live results

- Desktop OAuth consent completed with Gmail read-only and send scopes.
- Non-interactive reconnection and a forced refresh-token exchange succeeded.
- A message sent through Gmail web arrived in the dedicated service inbox, not spam.
- The real DeepSeek extraction ran without extraction fallback.
- The generated reply failed the completeness guard; the deterministic fallback was used.
- The fallback accurately withheld the pack and requested missing facts/documents.
- The outbox sent the reviewed fallback through Gmail API. The sender's Gmail web UI
  displayed it in the original conversation, with two messages at that point.
- Reprocessing the inbound message returned `duplicate_ignored`; case, event and
  outbox counts remained one each. A second dispatch produced no send.
- Gmail retained the thread, In-Reply-To and References headers, but rewrote Message-ID.
  The old RFC-ID-only reconciliation query returned no match: this was a real defect
  not exposed by the fake-provider contract.
- The adapter now includes an X-Visa-Agent-Message-ID correlation header and, when
  RFC-ID lookup fails, scans metadata for at most 100 sent messages. Multiple matches
  fail closed; missing matches still require manual review, never automatic resend.
- One additional, explicitly labelled transport diagnostic message verified that Gmail
  retained the custom header and the adapter recovered the sent message despite the
  rewritten RFC Message-ID. This was not a second applicant response.

## Fixes and verification

- Fixed a sandbox-runner address check to accept a single display-name mailbox while
  rejecting additional recipients. The rejected attempt failed locally before Gmail
  was called; exactly that row was manually restored for the reviewed retry.
- Fixed premature FINAL_CONFIRMATION progression: that stage is now entered only
  when final applicant confirmation is the sole failed gate. Historical test state was
  not rewritten, preserving what the first run actually observed.
- Added regression coverage for rewritten Message-ID recovery, sent-label checking,
  the correlation header and premature final-confirmation progression.
- Full suite after conversation changes: **136 passed**. Source type check: passed (42 files).

## Attachment conversation B

- Seven fixture PDFs were uploaded through the owner's Gmail web account and received as real MIME attachments.
- DeepSeek correctly identified the invitation/trip date conflict and missing translation without extraction or reply fallback.
- A correction reply with two PDFs resolved those two issues and produced `awaiting_confirmation`, with no delivery generated.
- The original confirmation request omitted the actual summary. This was caught before sending and fixed: confirmation messages now render the stored facts and current document list deterministically.
- The reviewed pending request was updated before its first send. Gmail web visibly received the complete summary.
- Final confirmation was initially paused. Later the old draft was replaced with ordinary
  assent, with no test disclaimer or exact command. Because the historical summary lacked
  the new confirmation fingerprint, the agent safely sent a fresh summary rather than
  treating legacy state as verified consent.
- Gmail's own suggested reply, `Yes, everything is correct.`, confirmed that delivered
  summary. Real DeepSeek extraction and ready wording ran without fallback. The complete
  ZIP was visibly received in the sender's Gmail conversation.
- Pre-send inspection found a superseded invitation inside the generated ZIP. Generation
  now excludes superseded/unaccepted source documents from the customer package/index;
  full history remains in the audit data. The unsent first ZIP was preserved, a reviewed
  revision was generated, and the previous delivery record was archived. The sent ZIP
  contains eight current source documents, six rendered PDF outputs and one JSON output.
- All six generated PDF pages were rendered and visually checked before dispatch; ZIP
  integrity was checked locally. Recipient-side byte-for-byte download verification is
  separate and must not be inferred solely from the visible Gmail attachment.
- Gmail's recipient-side ZIP viewer successfully opened the archive and showed the six
  generated PDFs, JSON answers, and eight current supporting documents, including only
  the corrected invitation. The old invitation was absent from that received archive.
- A real process-crash experiment exited with code 75 immediately after Gmail accepted
  the final message, before local SENT commit. State remained SENDING. A new process
  recovered the provider message through reconciliation and marked it SENT. A subsequent
  dispatch returned no messages. This verified this specific crash window, not all outages.

## Local deployment persistence

- The former Docker command reset data on every startup. It now seeds only when no
  database exists, and Compose uses a named runtime volume.
- The existing runtime was copied while stopped to a private local backup, migrated into
  the volume, then upgraded and restarted. The database SHA-256 matched before migration,
  after upgrade and after restart; the health endpoint returned OK.
- Latest local automated suite: **157 passed**; strict source typing and lint passed.

## Ordinary-language conversation C — no test markers

- Subject and body contained no test marker, hidden facts or prescribed command. The fictional applicant asked casually in Chinese about a November holiday and said they worked in Shenzhen, held a Chinese passport and had not booked a hotel.
- The first interpretation incorrectly mapped passport country to application location and workplace to home address, then escalated the case. Its pending reply was **not sent**.
- After verifying zero send attempts and zero deliveries, the first SQLite state was preserved as a separate local evidence database before a reviewed replay. This was not an uncertain-send retry or deletion of send history.
- The extractor instructions and guard now distinguish those fields. An incomplete month cannot become an invented exact date or, by itself, a reason to lock the case into manual review.
- The revised run stayed in intake, retained unknown location/address/dates as unknown, and sent a Chinese reply asking only three next-step questions. This response used the reviewed deterministic fallback; it is not evidence that every model-written reply is accepted.
- The sender's Gmail web thread visibly contains the ordinary enquiry and the delivered Chinese reply.
- Two real-model repetitions of four ordinary/adversarial text scenarios passed **8/8 scenarios, 56/56 checks**. Two replies used safe fallback wording. This is a small regression set, not a measured general production accuracy rate. See `eval_output/natural_conversation_2026-09-04.json`.
- A second pass added reply-length and internal-field-label checks: **8/8 scenarios, 72/72 checks**, two fallback replies (`eval_output/natural_conversation_2026-09-04-v2.json`). Manual reading still found an unsupported reassurance about travel timing, which those mechanical checks missed. An additional guard and regression test now reject that assurance. Passing this matrix therefore must not be presented as perfect advice or perfect UX.
- The full three-step real-model fixture workflow passed **39/39 state/delivery checks** and generated its local pack, but its stricter no-fallback acceptance criterion **failed**: both model-written messages needed safe replacement, and the confirmation message is deliberately deterministic. This is recorded as a failed acceptance run in `eval_output/deepseek_workflow_2026-09-04-v2.json`, not relabelled as a pass. The older intermediate report counted the deterministic confirmation as a model reply; the evaluator has now corrected that metric.
- New automated coverage includes natural assent, receipt versus consent, quoted/negative/conditional confirmation, unsent Gmail summaries, corrected facts invalidating confirmation, and unclassified PDFs being withheld.

## Ordinary PDF extraction — local real-model experiment

- Added bounded text extraction and local OCR, followed by typed DeepSeek proposals.
  Every accepted fact retains its page and a checked source quotation. This is extraction,
  not verification of document authenticity.
- Four fictional ordinary documents were tested: an identity-information summary,
  conference invitation, student letter, and image-only scan of the student letter.
  The corrected run passed all four specimen checks, including actual OCR execution.
  See `eval_output/natural_documents_2026-09-04-v2.json`.
- The first run missed the invitee's name. Extraction instructions now name the allowed
  fields explicitly, and missing required identifiers trigger review. The first failed
  report is retained. An identity summary expressly labelled as not an identity document
  must remain withheld; its review flag is a correct safety result, not a failure to waive.
- Passport expiry must cover the stay and must come from an accepted travel document;
  an expiry date mentioned in a different letter cannot satisfy that gate.
- Unknown/unreadable document classification no longer becomes an unsupported demand
  for translation. It remains blocked for classification instead.
- Limits: 10 MB, 20 pages, at most five OCR pages, bounded extracted text and subprocess
  timeouts. These four specimens are not a general accuracy benchmark or a Gmail
  attachment end-to-end test. Offline console extraction remains fixture-only.

## Ordinary attachments and correction D — real Gmail

- An ordinary Chinese conference enquiry was sent through the owner's Gmail account with
  four previously approved fictional PDFs: an identity summary, invitation, student letter
  and image-only student-letter scan. Neither subject nor body contained test commands.
- The first intake exposed spaced/shared-year date parsing and non-verbatim model quotation
  failures, causing an unnecessary review escalation. The inadequate reply was **never sent**.
  After checking zero send attempts and zero deliveries, the original database was preserved
  as `first-attempt-unsent.db`; the same inbound message was replayed into corrected active state.
- Date evidence now supports explicit shared years and Gmail line wrapping, but never fills a
  missing year from the clock. A bounded extraction retry handles invalid model quotations.
  Study location cannot establish nationality, and corrections cannot erase contrary PDF facts.
- Text extraction and actual OCR completed. The invitation/itinerary date conflict was flagged;
  the explicitly non-identity passport summary stayed blocked. The reply answered the customer's
  booking question using a GOV.UK source checked on 2026-09-04, with a transit exception and
  a bounded answer-review window. It then asked at most three selected next actions.
- A second natural email corrected departure to 2026-11-13, retaining arrival 2026-11-09.
  The invitation conflict resolved without re-uploading any of the four attachments. The reply
  explicitly acknowledged the correction and asked only outstanding questions. Both reviewed
  service replies were sent and visually verified in the recipient's Gmail thread.
- Both replies used deterministic safe fallback wording. No passport was fabricated or accepted
  from the summary; this case has **zero final deliveries**, intentionally. It does not prove
  an ordinary-document final-pack journey, document authenticity or unrestricted advice accuracy.
- Local real-model replay: three repetitions of the two-step journey passed **48/48 checks**
  (`eval_output/natural_journey_2026-09-04-v4.json`). The initial failed report is retained.
- Local automated suite: **183 tests passed**, lint and strict typing passed. A separate stability
  run passed 20 complete deterministic workflows with identical ZIP hashes and 100 concurrent
  console reads. These are bounded internal tests, not a production reliability guarantee.
- The updated extraction corpus, negative-fact corpus and perturbation corpus have separate
  scored reports in `ACCURACY.md`. Failures are preserved, not removed after a passing run.
- The final local Docker rebuild was healthy; its existing runtime database SHA-256 was
  `562f693a0cc6ea2b5406521e960238a83b9c8ad214eab8faac9b8b8132e76653` both before and
  after the update. No runtime reset or unattended Gmail sending was performed.

## Remaining acceptance work

The registered-sender automatic rollout is documented in `GMAIL_AUTOMATIC_SERVICE.md`. It resolved
the owner's unprocessed ordinary university-mailbox enquiry and now runs as a local supervised
60-second service. Provider-side reply acceptance was checked; arbitrary-sender/public intake and
recipient-inbox delivery observation are not claimed. This supersedes the earlier absence of any
running Gmail worker, not the outstanding production and consent requirements.

### Send uncertainty and provider-auth rejection follow-up

- Found and corrected a duplicate-delivery risk: a Gmail send timeout or HTTP 5xx previously
  became an automatic retry even though acceptance could have occurred. Those outcomes, missing
  send response IDs and unclassified send transport failures now remain `SENDING` with a recorded
  attempt and no scheduled resend. Reconciliation can recover a matching provider ID; a missing
  match requires manual investigation, never an automatic retry.
- Explicit HTTP 429 and rate-limit-specific HTTP 403 rejections retain bounded backoff. Permission,
  domain-policy and daily-quota 403 responses stop automatic delivery. Error bodies are not logged.
  Classification was checked against Google's official error guide:
  https://developers.google.com/workspace/gmail/api/guides/handle-errors
- Local fault injection verifies an accepted-send/lost-response outcome cannot be dispatched
  again and can recover through reconciliation. These injected timeout/5xx/quota cases are **not**
  real provider outages. Complete local suite at this increment: **193 passed**, lint and typing pass.
- A real, read-only Gmail profile request with a deliberately invalid token returned HTTP 401,
  mapped to permanent authorization rejection. See `eval_output/gmail_invalid_auth_2026-09-04.json`.
  No saved credentials were loaded, no token was revoked, and no email was sent. This verifies
  only invalid-credential rejection, not refresh failure, OAuth revocation recovery or send delivery.

### Outstanding scope

- Identity-warning regression, 2026-09-04: six text-level examples (`SAMPLE PASSPORT / NOT VALID
  FOR TRAVEL`, `SPECIMEN`, demonstration-only wording, full-width dummy-passport wording and
  two Chinese warnings) initially bypassed the review flag even with grounded facts. Six
  regression tests reproduced that failure before the fix. Negative-warning detection now
  covers those phrases and Unicode compatibility forms; quotation grounding is unchanged.
  This is a local validator fix, not OCR coverage, document authentication or a live final-pack
  journey. Unmarked forged documents are not proven detectable by this check.
- The ordinary-document final-pack journey needs an authorized volunteer and explicit consent
  for local/DeepSeek processing of their materials. That participation has been requested;
  no real identity documents have been solicited for immediate upload or processed by this
  regression. The existing expressly non-identity summary remains blocked, not relabelled as
  a valid passport to obtain a passing final delivery.

- Unstructured conversation requires broader multi-turn testing: deferring unknown dates,
  answering policy questions with current sources, difficult corrections and language switching.
- Natural confirmation currently recognises a conservative set of clear expressions with
  context/fingerprint checks. It no longer demands one exact command, but arbitrary paraphrases
  are not yet comprehensively covered; uncertain wording asks again rather than releasing a pack.
- Ordinary text/scan extraction now has a small local real-model test and a real Gmail
  attachment/correction test; broader document coverage and translation matching remain unfinished.
- The final natural confirmation and ZIP delivery passed in the fixture-document thread;
  this must not be described as full ordinary-document end-to-end acceptance.
- Live 429/5xx, revocation and permanent provider errors remain untested; automated
  contract tests must not be represented as those live experiments.
- The runner now supports `prepare --watch` for repeated intake preparation, protected by
  a per-state process lock. It never auto-sends; responses still require reviewed dispatch.
  No unattended production deployment or WhatsApp connection is claimed.

## Repeating a bounded experiment

Use `scripts/gmail_sandbox.py --help`. Supply the dedicated mailbox, allowed sender,
optional exact subject and a private state directory under `data/`. Test labels are not required
in customer mail; synthetic status belongs in the operator's test records. Run `prepare`,
inspect the generated outbox text, then explicitly run `send-reviewed` (one due reply
per invocation). Reuse the same directory for replay; never delete state to retry an
uncertain send. Do not use this runner with real applicant data.
