# Gmail sandbox runbook

This experiment is optional and separate from the credential-free interviewer Demo. Use a dedicated
test Gmail account and synthetic applicant data only. Never use a real applicant mailbox or commit
OAuth files.

## One-time Google setup

1. Create or select a Google Cloud project and enable the Gmail API.
2. Configure the OAuth consent screen for testing and add the dedicated test account.
3. Create an OAuth Client ID of type **Desktop app**.
4. Download the file to `.secrets/gmail_credentials.json`.
5. Run `uv sync --extra dev --extra live`.
6. Run `uv run visa-agent gmail-auth` and approve the two requested scopes in the browser.

The application requests Gmail read-only plus send access. The refresh token is written to
`.secrets/gmail_token.json` with owner-only file permissions. Both locations are ignored by Git.
Changing scopes requires deleting the local token and authorizing again.

Google's current testing quickstart requires a Cloud project, Gmail-enabled account, Desktop OAuth
client, and one interactive consent. The [official Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python)
also makes clear that this installed-app flow is for testing; production deployment needs a separate
authorization design. Gmail groups replies only when the subject matches and RFC `References` plus
`In-Reply-To` headers are correct, as documented in the
[official sending guide](https://developers.google.com/workspace/gmail/api/guides/sending).

## Evidence checklist

Do not mark E-04 complete until one redacted report records all of the following with synthetic mail:

- OAuth authorization and token refresh;
- raw MIME download and PDF attachment ingestion;
- correct provider thread, `Message-ID`, `In-Reply-To`, and `References` headers;
- one blocked reply, one correction reply, and one ready reply with the ZIP attachment;
- provider redelivery with no duplicate case, reply, or pack;
- 429/5xx retry, revoked credential, and permanent 4xx behaviour;
- sent-mail reconciliation after a simulated worker crash;
- redacted provider IDs and timestamps, with no token, raw body, or personal data in the report.

The current repository passes a fake-provider contract for these boundaries. That is automated
simulation, not Gmail sandbox evidence.

Initial live testing was performed on 2026-09-04. See [the redacted evidence report](GMAIL_LIVE_EVIDENCE.md)
for observed successes, the Gmail Message-ID rewrite defect and fix, and remaining acceptance work.
E-04 remains incomplete until the entire checklist above is satisfied.

## Ordinary-language trials

Customers do not need a `VISA-DEMO` subject/body marker, fixture facts, or a fixed confirmation
command. The operator restricts access by the dedicated mailbox and allowed sender. `--subject`
is optional: include it for one isolated thread, or omit it to accept arbitrary subjects from that
sender. Do not create a second state directory over already-processed mail: that can duplicate
replies. Changing the scope of an existing bound directory is deliberately rejected.

The current runner is invoked manually (`prepare`, review, `send-reviewed`) and sends at most one
due reply per invocation. It is **not yet an unattended interviewer-facing inbox service**.

Natural final consent is accepted only against the current, unchanged summary; Gmail additionally
requires that summary's outbox record to be SENT. A receipt such as “收到”, a question, a quoted
confirmation, or a simultaneous correction cannot release the pack. Corrections require a fresh
summary. Unknown ordinary PDFs are held for manual classification, not assumed to be verified.

Run the ordinary-text real-model regression without sending email:

```bash
uv run python scripts/natural_conversation_eval.py --runs 2
```
# Continuous preparation and crash recovery

`scripts/gmail_sandbox.py prepare --watch` repeatedly prepares replies within the bound
sender/subject/state directory. Polling defaults to 60 seconds and never automatically
sends. Keep using `send-reviewed` only after checking the pending reply. Each action
uses a non-blocking state-directory lock. Complete inbox pagination is bounded to 100
messages; an oversized result stops explicitly instead of silently dropping older mail.

After an interrupted send, use the same scope and state directory with `reconcile`.
A missing or ambiguous provider match requires manual investigation; do not delete
the database or make a fresh directory to resend. For an explicitly reviewed synthetic
crash experiment only, `send-reviewed --crash-after-send` terminates immediately after
provider acceptance (exit code 75). It must be followed by reconciliation, not a forced retry.
