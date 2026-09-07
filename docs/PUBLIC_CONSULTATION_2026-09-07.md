# Public consultation before personal-data processing

The Gmail preflight can now send reviewed public guidance without asking for
processing consent first. Introductory material questions and narrowly scoped
public questions use the existing grounded-answer library, not an LLM. A short
holiday follow-up is supported without repeating the purpose question.

Public replies do not grant consent, extract a profile, retain the inbound body,
read attachments or send data to a model. Minimal routing metadata and canonical
outbound text remain stored for isolation and duplicate prevention. Attachments,
detected personal assessment requests and unsupported/mixed requests retain the
existing processing gate. This is a conservative routing boundary, not a general
personal-data detector or a claim that all free-form consultation is supported.

The new outbound type is canonical, recipient/thread/epoch bound and invalidated
by withdrawal. Provider message replay does not generate duplicate sends.

Verification: 201 passing selected Gmail/consent tests, plus 112 passing consent,
recovery and mixed-processing tests (overlapping selections); ruff and mypy pass.
Capture transport verified public inquiry → holiday follow-up → duplicate poll,
with no model extraction or document reads. Personal-data and attachment cases
still request permission. No new real-customer messages were manually sent.

Existing source-bound paid-provider reports predate this change and have not been
relabeled current. This is not a full-release or perfect-agent acceptance claim.

Introductory guidance was checked against the [GOV.UK supporting-document guide](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk)
on 7 September 2026. Runtime freshness checks still apply.
