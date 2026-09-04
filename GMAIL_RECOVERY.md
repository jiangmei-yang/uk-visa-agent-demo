# Gmail credential and evidence-query recovery

This is a bounded reliability improvement, not a claim of generally perfect delivery or a
replacement for the [unfinished acceptance ledger](VALIDATION.md). Local fault injection,
real read-only OAuth observations and actual recipient delivery are different evidence classes.

## Credential persistence and explicit reconnection

The former token writer truncated the active file before writing its replacement. It now writes
a same-directory, owner-only temporary file, flushes and fsyncs it, closes it, then atomically
replaces the active token. A handled pre-commit failure preserves the previous bytes and removes
the temporary file. An abruptly terminated process can leave a private temporary file; it does
not leave a partially written active token. This does not claim power-loss durability, protection
against a malicious local administrator, or coordination between multiple token writers.

Do not delete a credential or case database to recover. For an expired/rejected refresh token or
malformed local token, stop the supervised worker first, then explicitly run:

```bash
uv run visa-agent gmail-auth --reauthorize --mailbox service@example.test
```

Replace the example with the **existing dedicated service mailbox**. Keep the existing client
file, token path and registered-sender state directory. The command skips the unusable token,
requests new consent, builds the service and verifies the selected mailbox before replacing the
token. Cancellation, unusable consent, service construction/profile failure and a different
mailbox preserve the old token. The client configuration and token cannot resolve to the same
path. A successful reconnection does not authorize a change to the registered sender or mailbox.

Ordinary background refresh never opens a browser. Without `--reauthorize`, a refresh or parsing
error is surfaced rather than automatically initiating another consent flow. Google documents
that refresh-time `invalid_grant` can require renewed user consent; see its
[installed-app OAuth guidance](https://developers.google.com/identity/protocols/oauth2/native-app#errors).
Restart the same supervised worker only after authorization succeeds. Do not run reauthorization
and a credential-writing worker concurrently.

## An unavailable search is not a negative delivery result

A previous send can be accepted by Gmail while its local row remains `SENDING`, for example
after a process exit. Evidence lookup must distinguish these outcomes:

| Lookup result | Local result | May automatically resend? |
|---|---|---|
| Exactly one accepted provider copy | Record `SENT` | No |
| No copy or multiple copies after a completed lookup | `AMBIGUOUS`; operator investigation | No |
| HTTP 401, a known permission-related 403, or typed Google refresh failure | Keep `SENDING`; restore access and query again | No |
| Temporary network/rate-limit failure | Keep `SENDING`; query later | No |
| Other permanent/unknown lookup failure | `AMBIGUOUS`; operator investigation | No |

The permission classification applies only to sent-evidence lookup. A send rejected with 401/403
still follows the permanent-send-failure boundary; this change does not create a blind retry path.
Unknown or mixed 403 reasons are not assumed to be recoverable access failures. A partial marker
scan cannot be accepted just because one matching message was seen before the query failed.

In automatic `serve`, an `ACCESS_REQUIRED` outcome stops the cycle before model work, intake or
dispatch. The watch heartbeat records `ReconciliationAccessError` and retries at the existing
poll interval, without publishing the provider's exception body. A runner/watch regression first
failed all three new cases; after the fix, the **36-case** query-recovery file passes, including
an error heartbeat followed by an idle cycle that records the prior send without sending again.
The manual `reconcile` command's output/exit behaviour is unchanged.

Access recovery may record a past accepted send even after the customer paused preparation or
started a new control epoch. It does not restart preparation, clear a review hold, change the
original reply, increment its send attempt, or authorize a new/final pack. Existing `AMBIGUOUS`
rows from an older version are not silently reclassified or requeued.

## Isolated regression evidence

The first credential test set produced **12 failures / 8 passes** against the old implementation.
The first sent-evidence recovery test set produced **7 failures / 11 passes**. Those failures led
to the changes above; later green tests are exposed regression results, not unseen holdout scores.

- [Credential tests](tests/unit/test_gmail_auth.py): failed encoding/fsync/replace, abrupt exit,
  private replacement bytes, path safety, valid/expired credentials, explicit reauthorization,
  cancellation, service/profile rejection and wrong-mailbox preservation. They use synthetic
  files and substituted Google components, not a real revoked account.
- [Evidence-query recovery](tests/integration/test_gmail_reconciliation_recovery.py): real
  Gmail adapter/sender and SQLite boundaries with a fake provider, including database reopen,
  partial marker scans, old preparation epochs, failure classification and zero resend.
- [Earlier runner tests](tests/integration/test_gmail_auth_recovery.py): failures before credential,
  profile and discovery stages preserve the pending case/reply. They are not live recovery proof.

## Real isolated refresh observation — 2026-09-04

One fixed, three-stage run at **13:15:32 UTC** passed. No stage was retried to select a passing
result. The [sanitized report](eval_output/gmail_isolated_refresh_2026-09-04.json) records:

| Isolated stage | Observed Google response |
|---|---|
| Force local access-token expiry on a valid copy | Token refresh 200; read-only profile 200; expected mailbox |
| Replace only the copied refresh token with an invalid value | Token endpoint 400 `invalid_grant`; zero profile requests; copy not overwritten |
| Restore a copy of the original valid credentials and force local expiry | Refresh 200 and profile 200 again |

The wrapper recorded three token transport operations and two profile transport operations,
**zero sends**, no message-body requests,
no revocation/consent calls and no live database operations. The original client/token bytes
were unchanged. Temporary copies used a private directory and owner-only files, then were removed.
The probe uses the production credential-loading/refresh/atomic-save function with a **probe-only**
restricted transport, bundled discovery, no redirects, no implicit credential refresh and no
upper-layer repeated call per stage. These are **not wire-level request counts**: the underlying
`httplib2` client may internally reconnect/retry. No packet trace was collected, and configured
SDK timeouts are not a hard end-to-end deadline. The 47 synthetic tests replace this underlying
HTTP client. The observation does not validate production transport timeouts or prove the absence
of socket-level retries; those claims were removed during independent evidence review.

To reproduce with an authorized dedicated account, use a **new** evidence path:

```bash
uv run python scripts/gmail_refresh_probe.py \
  --credentials .secrets/gmail_credentials.json --token .secrets/gmail_token.json \
  --mailbox service@example.test --report eval_output/a-new-refresh-observation.json
```

This does **not** prove natural expiry, revocation of the account's refresh token, successful user
reconsent, live-worker recovery or recipient delivery. Do not combine the isolated result with
mocked runner tests to claim those missing real-world events occurred.

## Guided lab download parity

The guided lab previously checked the gate and file existence but did not verify the registered
revision/path/hash. Its first isolated integrity test run produced **11 failures / 1 pass**.
All **12** focused cases pass after the fix. Both the availability indicator and download now
require the current delivery registry, no held update, a permitted file path and matching bytes.
The response returns those verified bytes with `Cache-Control: no-store`, not a later file reread.
See [the regressions](tests/integration/test_lab_pack_integrity.py). This does not authenticate the
synthetic applicant documents or supply ordinary-material final-delivery evidence.

## Still not proved

Real credential revocation/reconsent and live-worker recovery, provider-induced 429/5xx, natural
history expiry, recipient-side revised-pack delivery and independent nontechnical operation are
still open. The runner's pack-generation failure/acknowledgement window and a full broken-page →
operator-rescan → restart experiment remain separate follow-up work. Do not infer those results
from an invalid-token HTTP 401 probe, a healthy reload, or an increasing unit-test count.
