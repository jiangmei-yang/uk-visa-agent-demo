# WhatsApp sandbox runbook

The implemented Demo adapter targets Twilio WhatsApp Sandbox because it supports functional testing without
a WhatsApp Business Account or registered sender. It remains a provider sandbox, not a production
deployment. The repository includes a free provider-only TryCloudflare tunnel launcher; the review
console and case APIs are not exposed by that gateway.

## Current preparation status

The repository has automated contracts for signed inbound form payloads, MessageSid idempotency,
text/PDF intake, authenticated Twilio media download, durable fast-ack queuing with expiring worker
leases, channel-isolated outbox replies, provider error classification, and the 24-hour customer
service window. A Twilio account, HTTPS tunnel, joined device, evaluated live model, and real message
exchange are still required before E-05 is complete.

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
