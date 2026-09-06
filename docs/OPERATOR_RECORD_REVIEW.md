# Local Gmail record-review commands

Development operator tooling, not a finished nontechnical review interface.
Use only for a case you are authorized to inspect. Local terminal/filesystem
access is the security boundary; the `actor` text is an audit label, not a login.
These commands never send mail, grant processing consent or confirm a summary.

## 1. Inspect without approving

Explicitly stop the Gmail worker using your normal operating procedure first.
The commands acquire its `worker.lock`; they will refuse if another worker owns
the directory. They do not stop or restart a worker themselves.

Choose the existing state directory containing `sandbox.db` and the case ID.
The database must already exist; missing, empty and symlink database targets are
rejected. Use a new private output filename to avoid overwriting an earlier plan.

```sh
umask 077
visa-agent record-review-plan --state-dir /absolute/path/to/gmail-state --case-id CASE_ID > review-plan-new.json
```

The output includes the current record snapshot, source-registration issues,
intake checks, missing record details, policy digest, exact case fingerprint and
an **unapproved** `decision` template. Known-family passport details are conditional;
the same requirement is not applied to every friend/contact. Missing-detail output
does not cover every possible conditional application requirement.
It may contain personal information. Keep it local/private and do not commit it
to GitHub. Normal store migrations/lock-file handling may occur when opening an
existing state; inspection does not approve a case or create an outbound message.

## 2. Complete the decision only after review

Edit only the `decision` section:

- Enter the authorized operator's name and substantive overall rationale.
- Assess every current record exactly once. Describe what you checked and why.
- Choose `not_a_contact` for a travel record, `family` for a UK family member,
  or `other_contact` for a non-family contact. Do not misclassify a relative to
  bypass missing passport information.
- Set `applicable_details_checked` only after completing that assessment.
- For travel records, explicitly assess the relevant history scope and supplied
  date precision before setting `travel_history_scope_checked`.
- Do not modify case IDs, record IDs, case fingerprint or policy digest to make
  a stale plan pass. Generate a new plan and review changed information instead.

If information is missing or uncertain, leave the plan unapproved and obtain the
needed clarification. The command rejects incomplete assessments; it does not
turn them into customer facts or issue a legal decision. See
[the review boundary and remaining requirements](RECORD_REVIEW_REQUIREMENTS.md).
The informational `context` section is not trusted as authority on submission.

## 3. Submit explicitly

```sh
visa-agent record-review-apply --state-dir /absolute/path/to/gmail-state --decision-file review-plan-new.json
```

Submission re-reads the actual case under the worker lock, checks the case and
policy fingerprints, processing permission, state, sources and each assessment,
then saves the review/history atomically. Blank templates, added unsupported
decision fields, stale plans and changed policies cannot approve a case.

A success response reports `review_saved`, `mail_sent: false` and
`customer_confirmed: false`. Old customer confirmation context is cleared.
Resume the normal worker through your usual controls when appropriate; fresh
customer confirmations and all remaining document/delivery gates still apply.
No real service is restarted automatically by these commands.

## Current limits

This is a command-line operator workflow. A guided authenticated review UI,
complete missing-detail feedback to the adviser, broader conditional intake,
visual pack checks and real-provider/Gmail recipient acceptance remain unfinished.
Source registration does not independently reverify purged original email bodies
or prove that customer statements are true. Do not describe this tooling as a
completed or independently validated human-adviser product.
