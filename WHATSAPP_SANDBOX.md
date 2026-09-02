# WhatsApp sandbox runbook

The selected Demo provider is Twilio WhatsApp Sandbox because it supports functional testing without
a WhatsApp Business Account or registered sender. It remains a provider sandbox, not a production
deployment.

## Current preparation status

The repository has automated contracts for signed inbound form payloads, MessageSid idempotency,
text/PDF intake, authenticated Twilio media download, durable fast-ack queuing with expiring worker
leases, channel-isolated outbox replies, provider error classification, and the 24-hour customer
service window. A Twilio account, HTTPS tunnel, joined device, evaluated live model, and real message
exchange are still required before E-05 is complete.

## Provider setup

1. Create a dedicated Twilio test account and activate the WhatsApp testing environment/Sandbox.
2. Join the Sandbox from a test WhatsApp device using its displayed join phrase.
3. Expose only the webhook route over a temporary HTTPS tunnel; do not expose the review console.
4. Set `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_FROM`, and the exact public
   `TWILIO_WEBHOOK_PUBLIC_URL` outside Git.
5. Configure that exact URL as **When a message comes in** and configure a status callback.
6. Send synthetic text and one synthetic PDF; never use real applicant data.
7. Process one durable inbound batch with
   `uv run visa-agent inbound-worker --channel whatsapp_twilio --model <evaluated-model>`.
8. Send one due reply batch with `uv run visa-agent whatsapp-dispatch`.

Twilio's current [Sandbox documentation](https://www.twilio.com/docs/whatsapp/sandbox) says it is
for testing/discovery, uses a shared number, requires each device to join, limits sending to one
message per three seconds, and expires a joined Sandbox session after three days. A user message
opens a 24-hour customer-service window for free-form replies. The
[official webhook security guidance](https://www.twilio.com/docs/usage/security) recommends SDK
signature validation using the exact configured URL, form parameters, signature header, and account
Auth Token; this is the contract implemented here.

## Evidence checklist

- valid signature accepted and a one-character mutation rejected;
- duplicate MessageSid produces no duplicate case or reply;
- text and one PDF media message reach the shared workflow;
- non-PDF, multiple media, oversized media, and non-Twilio media URL fail closed;
- a reply within 24 hours arrives on the joined device;
- a post-window free-form reply is stopped and visible as failed;
- 429/5xx retry and permanent 4xx behaviour are recorded;
- simulated worker crash replays safely;
- final pack remains an Email/review-console handoff, not an unprotected WhatsApp ZIP;
- redacted timestamps/SIDs are recorded without phone numbers, Auth Token, or message bodies.
