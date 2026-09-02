# Security and privacy

## Demo guarantees

- All committed people, addresses, organisations, IDs, financial statements, and documents are
  synthetic and visibly labelled.
- Secrets, OAuth credentials, API keys, and tokens are excluded from Git.
- The default path performs no provider calls and sends no Email or WhatsApp message.
- Pack downloads are restricted to the configured output directory.
- Document content is treated as untrusted evidence, never as an instruction channel.
- WhatsApp webhooks fail closed without explicit configuration, validate the provider signature
  before parsing, reject duplicate form keys and untrusted media hosts, then enter a durable queue.
- Successfully processed queue payloads are cleared; retry/dead-letter payload retention remains a
  synthetic Demo limitation and must have a production retention policy.

## Production controls still required

- Explicit applicant consent and a jurisdiction-appropriate privacy/retention notice.
- Authentication, case-level authorisation, operator roles, and access audit logs.
- Encrypted database/object storage, TLS, expiring signed downloads, backup and restore tests.
- MIME sniffing, filename normalisation, malware scanning, decompression limits, OCR sandboxing, and
  maximum file/page/image dimensions.
- PII-redacted observability; no raw documents or message bodies in routine logs.
- Configurable export/deletion, legal-hold handling, and verified retention jobs.
- Least-privilege Gmail scopes and independently reviewed Gmail/Twilio/WhatsApp data-processing terms.
- Prompt-injection, exfiltration, fabricated-evidence, and model-outage evaluations before live use.

Report vulnerabilities privately to the repository owner; do not open an issue containing applicant
data or credentials.
