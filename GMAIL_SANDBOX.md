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
