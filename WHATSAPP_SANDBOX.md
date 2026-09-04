# WhatsApp sandbox runbook

The implemented Demo adapter targets Twilio WhatsApp Sandbox because it supports functional testing without
a WhatsApp Business Account or registered sender. It remains a provider sandbox, not a production
deployment. The repository includes a free provider-only TryCloudflare tunnel launcher; the review
console and case APIs are not exposed by that gateway.

## Ordinary PDF worker wiring

An audit found that `inbound-worker` constructed its workflow without a document reader, so the
generic CLI path inherited the offline fixture parser. The dedicated Gmail runner had already
configured its natural reader; this gap affected the generic WhatsApp worker path. The DeepSeek
CLI now explicitly uses `NaturalPDFReader` for text extraction/OCR and grounded document proposals.
The optional OpenAI CLI currently has no document-model adapter: it retains attachments for manual
classification with a configuration reason instead of interpreting fixture markers as evidence.

Two command-level integration tests enqueue an ordinary one-page fictional student letter, execute
the actual CLI, and inspect the retained document and queue outcome. A stub document model returns
page-grounded classification/name evidence; this tests wiring, not real-model accuracy or Twilio
media delivery. The PDF was rendered and visually checked as readable. An initial stub omitted the
required name evidence and was correctly held; the test then supplied the name actually present
on the page without weakening the rule. The full local suite passes 340 tests, lint and typing.
Continuous worker operation is now available below; real device exchange remains unverified.

## Continuous local processing

With the gateway and this worker using the same `VISA_AGENT_DATABASE`, and the Twilio/DeepSeek
environment values configured, run:

```bash
uv run python scripts/whatsapp_service.py --model deepseek-v4-flash --interval 10
```

Use `--once` for one supervised cycle. The script does not create an account, join a phone, start
a gateway/tunnel or open a browser. Leave its terminal running; stop with Ctrl-C. Missing settings
fail before a provider client is created. This is not an installed always-on service. Do not run
the manual inbound/dispatch batch commands concurrently with this loop. Loop instances sharing a
database are protected by a dedicated local lock, while the gateway remains able to enqueue.

Each cycle first investigates old uncertain sends, then processes up to 20 WhatsApp queue items.
Any unfinished item, including a failed one needing operator repair, withholds all automatic
WhatsApp replies in this small sandbox deployment. Once intake drains, obsolete never-attempted
drafts are retained as FAILED/withheld and at most one current reply is attempted. A final sender
check refuses obsolete replies, pending/held intake, attachments and final-pack messages. The
existing free-form deadline and uncertain-send semantics still apply. The loop waits at least
five seconds between cycles; this is not a claim to satisfy every provider/account rate limit.

`ready` rows remain pending for operator-reviewed final handoff. Lost-SID recovery remains manual;
the loop cannot turn uncertainty into a safe resend. Global pause on one failed queue item,
arrival/send race windows, operator recovery UI, permanent hosting and end-to-end secure handoff
remain limitations. A completed cycle or provider SID is not proof of device receipt.

Four local integration tests exercise real workflow/queue/outbox components with a capture Twilio
client: intake drain/latest-only reply, failed-intake hold, uncertain-send non-repetition, and
Email/final-pack exclusion. No actual WhatsApp service was started for these tests.

## Current preparation status

The repository has automated contracts for signed inbound form payloads, MessageSid idempotency,
text/PDF intake, authenticated Twilio media download, durable fast-ack queuing with expiring worker
leases, channel-isolated outbox replies, provider error classification, and the 24-hour customer
service window. A Twilio account, HTTPS tunnel, joined device, evaluated live model, and real message
exchange are still required before E-05 is complete.

Run `uv run python scripts/check_live_setup.py` from the project to check local prerequisite
presence without network calls or printing secrets. It reads the current process environment,
not a copied `.env` file. A true check means presence/URL shape only, not credential validity,
Sandbox membership, public reachability or actual receipt on a device. The status callback
variable is included in `.env.example`; export the values into both gateway/worker processes.

## Provider setup

1. Create a dedicated Twilio test account and activate the WhatsApp testing environment/Sandbox.
2. Join the Sandbox from a test WhatsApp device using its displayed join phrase.
3. On macOS, double-click `START_FREE_WEBHOOK_TUNNEL.command`. Copy the exact generated HTTPS URL;
   the launcher exposes only the provider webhook application, not the review console.
4. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, and the exact public
   `TWILIO_WEBHOOK_PUBLIC_URL` outside Git.
5. Configure that exact URL as **When a message comes in**. Set
   `TWILIO_STATUS_CALLBACK_PUBLIC_URL` to the same public host plus
   `/webhooks/twilio/whatsapp/status`. The sender adds a per-outbox correlation query and passes
   this status callback URL on each message creation request. It must not point to the intake endpoint.
6. Send synthetic text and one synthetic PDF; never use real applicant data.
7. Process one durable inbound batch with, for example,
   `uv run visa-agent inbound-worker --channel whatsapp_twilio --provider deepseek --model deepseek-v4-flash`.
8. Send one due reply batch with `uv run visa-agent whatsapp-dispatch`.

Twilio's current [Sandbox documentation](https://www.twilio.com/docs/whatsapp/sandbox) says it is
for testing/discovery, uses a shared number, requires each device to join, limits sending to one
message per three seconds, and expires a joined Sandbox session after three days. A user message
opens a 24-hour customer-service window for free-form replies. The
[official webhook security guidance](https://www.twilio.com/docs/usage/security) recommends SDK
signature validation using the exact configured URL, form parameters, signature header, and account
Auth Token; this is the contract implemented here.

The free tunnel uses Cloudflare's development-only
[Quick Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/).
Its random URL changes on restart, so both the Twilio webhook setting and
`TWILIO_WEBHOOK_PUBLIC_URL` must match the newly printed URL exactly each time.

## Evidence checklist

- valid signature accepted and a one-character mutation rejected;
- duplicate MessageSid produces no duplicate case or reply;
- text and one PDF media message reach the shared workflow;
- non-PDF, multiple media, oversized media, and non-Twilio media URL fail closed;
- a reply within 24 hours arrives on the joined device;
- a post-window free-form reply is stopped and visible as failed;
- explicit 429 rejection retries after bounded backoff; 408/5xx and transport uncertainty stop
  automatic resending and require provider-log investigation; permanent 4xx rejection is recorded;
- simulated worker crash replays safely;
- final pack remains an Email/review-console handoff, not an unprotected WhatsApp ZIP;
- redacted timestamps/SIDs are recorded without phone numbers, Auth Token, or message bodies.

## Uncertain outbound delivery

Timeouts, HTTP 408/5xx, missing response SIDs and unclassified SDK errors may occur after provider
acceptance. They remain `SENDING` with no scheduled resend. The Twilio adapter cannot currently
correlate a lost response to a unique provider message automatically; its reconciliation method
reports that limitation explicitly, causing `AMBIGUOUS` for manual investigation. Do not interpret
this as a search proving no message exists. Inspect Twilio logs before authorizing any retry.

HTTP 429 is different: Twilio documents that these rejected requests were not processed and may
be retried after backoff ([official 20429 reference](https://www.twilio.com/docs/api/errors/20429),
checked 2026-09-04). The local suite covers error classification and the dispatcher path from timeout
to investigation without a second send. These are simulated faults, not live WhatsApp evidence.

Provider acceptance (a returned SID) is not proof of device delivery. Signed delivery-status
callbacks are now persisted separately with duplicate suppression, account/address/SID checks and
order-independent status reduction. Conflicting successful/failed receipts are marked `conflict`.
The private review server exposes `GET /api/outbox/delivery-receipts`; the public gateway does not.
Raw form bodies and channel error text are not retained. Case deletion removes linked receipts.
SDK-signed local request tests pass; actual provider callbacks, operator UI presentation and
lost-SID automatic reconciliation remain unfinished acceptance work.

## Final-notice archive verification (local evidence, 2026-09-04)

An audit found that the dispatcher skipped archive verification for WhatsApp because that channel
does not attach ZIPs. Three new regressions reproduced a ready notice being sent with altered
archive bytes, a missing delivery registration, or a mismatched registered path. The dispatcher
now verifies the registered path and SHA-256 for every ready message, including WhatsApp, while
still attaching the archive only on the email path. The three failures now stop before the sender;
a valid-archive control confirms that WhatsApp remains text-only. The complete local suite passes
316 tests, with lint and typing passing. No Twilio account or device was exercised by these tests.

The media-download contract was also checked against the official
[Media resource documentation](https://www.twilio.com/docs/messaging/api/media-resource): authenticated
API retrieval returns the media content. Redirects remain disabled; this source check is not a
successful live PDF download. Secure applicant-facing final handoff and device receipt still need
end-to-end evidence; a ready text notification alone does not establish delivery of the materials.

## Reply window crossing during processing (local evidence, 2026-09-04)

The continuous worker formerly reused its cycle-start timestamp for dispatch after intake and
model/document processing. A regression with an inbound message whose window was open at cycle
start but expired at dispatch reproduced `SENT` through the capture sender. The worker now reads
the current UTC time after intake before dispatch. The same regression returns `FAILED`, does not
call the provider, retains the processed intake/case/outbox, and does not resend on a later cycle.
Existing in-window sending controls also pass. This is a deterministic local clock-boundary test,
not an actual Twilio expiry experiment or a guarantee against time passing during network transit.
All 345 local tests, lint and typing pass; the suite emits a Starlette/httpx deprecation warning.
No live WhatsApp service was started and no credentials or recipient enrollment were changed.
