# Employer conflict clarification and reviewed retry

Review replies now name the conflicting category (current employer, address or
telephone) and ask which information applies, allowing an explanation of job
changes, multiple roles or multiple contacts. They do not select a value or claim
review completion. Serious-history guidance is retained if both issues apply.

## Actual workflow boundary

An initial integration experiment failed: it expected a clarification to enter
the profile immediately, but the authoritative result was `human_review_case_held`.
Existing review cases intentionally hold new events before extraction. This
boundary was preserved, not bypassed to make the test pass.

The corrected test verifies the full **synthetic local** sequence:

1. Conflicting employer statements create a persisted review hold and a specific
   clarification request.
2. A later same-customer clarification is stored verbatim in held events. No
   employer fact or evidence is silently added during the hold.
3. An explicitly labelled fictional operator uses the existing fingerprint-bound
   reviewed-retry operation. The original clarification is queued for normal
   validation, not edited into the profile by the reviewer.
4. Processing that retry records the supplied employer and its retry source,
   returns to DRAFT preparation, and still leaves the pack blocked with both
   customer confirmations false. No ZIP is created.

No real customer or operator approval is implied. The operator interface remains
a local trusted operation, not an authenticated nontechnical review UI.

Renderer-only tests also cover a supplied-fact review snapshot and ensure its
wording does not claim the review is resolved. That branch is defensive rendering
coverage, **not evidence that held customer replies automatically update facts or
receive a fresh reply**. Actual held-event acknowledgement remains separate work.

Verification: 38 targeted message, employer workflow and reviewed-retry tests
passed (0.89s). Ruff and strict Mypy passed for 93 modules. State snapshots are
unchanged by rendering. No full-suite, new provider experiment, live Gmail,
deployment or final-release acceptance is claimed for this checkpoint.
