# Applicant processing permission

This is a technical execution boundary, not a claim of legal compliance, a full
privacy policy, or a provider data-deletion agreement. It is separate from
permission to submit a visa application, approval of a profile summary, final-pack
confirmation, preparation pause/resume and an operator's authorization to use an API.

## Implemented contract and limits

The registered Gmail runner configures a versioned processing scope in its local
database. The scope identifies the model provider/model and the exact notice.
An existing case with no matching grant is **unknown**, regardless of its stage,
old confirmations, mailbox allowlist or OAuth authorization. Merely configuring a
provider never supplies an applicant grant.

Before permission, the runner can read the scoped email envelope and latest body
in memory to identify a privacy control. Gmail's raw-message response includes MIME
attachment bytes: this is **not** a claim that those bytes never reach the process.
The boundary prevents local attachment materialization, OCR, model calls and
ordinary body/evidence/held-event persistence before consent. Only the minimum
contact/thread/provider message identifiers, necessary reply subject metadata,
notice and control audit are retained. A subject can itself contain personal text.
Existing copies in Gmail are not deleted or moved.

A dedicated reviewed notice identifies local storage and the external processing
provider. It explains that permission covers the thread's identified previously
deferred messages as well as subsequent preparation. The notice must actually be
SENT and the applicant's explicit agreement must include the public reference from
that current notice in the same statement. This reference binds case/scope/epoch;
it is not a password and is not required in subsequent ordinary consultation. An unsent draft, copied
phrase, old notice, other person's answer, profile confirmation or operator flag
must not create permission. SENT records prove sending, not that a person read or
understood the notice.

After a valid grant, deferred messages are fetched using their original provider
IDs and processed in provider receipt order. Consent is not an excuse to change
source IDs, invent a customer email or process unrelated historical correspondence.
The customer should not have to attach the same files again merely because the
first email arrived before the notice.

The permission reply is currently a pure control message. New facts or files mixed
into that reply are not automatically extracted; the notice asks for a separate
permission reply. Earlier deferred originals are still recovered. This usability
restriction remains a real limit, not evidence that arbitrary mixed email works.

## Withdrawal and secondary execution paths

Privacy controls must be inspected before ordinary business work or the existing
human-review retry queue. A withdrawal cannot be starved by a failed review job or
treated as an ordinary held correction to a finalized case. It changes a separate
processing epoch, invalidates summary confirmations and prevents stale business
replies from sending. Re-granting permission does not resume a paused preparation
or restore either summary confirmation.

Model extraction, direct document rereading, review/revision queue creation,
pack generation/cache access, download and outgoing business replies each consult
the current ledger. Dedicated notice/withdrawal receipts use their recorded static
body, never the ordinary case renderer. SENDING/AMBIGUOUS/SENT records remain
evidence: withdrawal cannot recall already transmitted data or justify resending
an uncertain message. Minimal provider-send reconciliation remains available.

Provider/model or notice-version changes require fresh permission. A document
retry must not silently select a different provider than the applicant was told
about. `--allow-model-processing` is an operator execution choice, not an applicant
privacy grant.

## Evidence and remaining scope

The implementation is verified with fictional envelopes, captured senders
and isolated databases. No real applicant permission is fabricated in those tests.
Existing reliability fixtures explicitly bypass the new protocol inside the Python
test harness to retain their original fault-isolation scope; that switch is not a
public CLI option. Such fixtures are not evidence of the new consent journey.
Production entry points must configure the processing scope by default.

Gmail `prepare` also uses the durable sync journal. Each cycle previews at most
100 raw candidates and then refetches at most 100 business messages; those are
separate budgets, not a total limit of 100 raw Gmail fetches. Business processing
and queued review wait until all discovered controls have been scanned.

The generic inbound worker retains `AWAITING_CONSENT` payloads and later resumes
the same source IDs only under the current matching grant. It is not Gmail's full
preflight implementation: webhook payloads and media may already be local, and
late/out-of-order originals may require normal chronological review. This avoids
silently clearing an unprocessed message, not all pre-consent storage.

This does not waive remaining acceptance: a consented ordinary-material recipient
journey, independent notice/usability review, retention/deletion operations and
provider agreements still require evidence. WhatsApp's pre-download consent flow
must be verified separately before claiming the same channel-level protection;
downstream model/send gates alone are not sufficient proof.
