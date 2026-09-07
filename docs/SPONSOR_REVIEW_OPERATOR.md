# Local sponsor-location review

This command is for a trusted local operator on macOS/Linux, not the applicant
and not a public authenticated reviewer portal. Filesystem access is the current
authority boundary; entering an actor name does not authenticate anyone.

1. Stop the Gmail worker normally before modifying its state. Do not bypass or
   delete its lock. Keep a private database backup using the existing runbook.
2. Run `visa-agent sponsor-review-plan --state-dir <state-directory> --case-id <case-id>`.
   The output includes private sponsor/source information. Save it privately as
   a JSON decision file; do not commit it to GitHub or paste it into public logs.
3. Read the original statements, identity epoch and both dimensions. An empty
   dimension cannot be approved. If values conflict, obtain the applicant's
   clarification and process its retained update through the existing reviewed
   retry procedure first. Do not edit the database or invent a new statement.
   After reading the registered sources, set `decision.selected_source_event_ids`
   for each conflicting dimension, for example
   `{"residence": "the-registered-clarification-event-id"}`. Leave it `{}` when
   there is no conflict. Only current-identity, unambiguous existing sources can
   be selected; a dimension with remaining conflict is still rejected. This is
   an explicit operator decision, never automatic latest-email-wins behavior.
   Original statements remain in the private case history. Context is informational;
   edits to it do not change the case or evidence requirement.
4. Only after actual review, fill `decision.actor`, a substantive
   `decision.rationale`, and set `decision.source_and_applicability_checked` to
   `true`. Do not edit case/policy fingerprints to bypass a stale-plan rejection.
5. Run `visa-agent sponsor-review-apply --state-dir <state-directory> --decision-file <private-json-file>`.
   If the case or policy changed, obtain a fresh plan and review it again.
6. Inspect `case_status` and `remaining_review_reason`. This operation removes
   only the location review hold; it cannot clear unrelated review reasons.
   Resume normal intake only when appropriate. Fresh applicant confirmations,
   document review and the separate final-delivery process are still required.
   Any new source or identity change invalidates the source selection and requires
   fresh review. Customer summaries and answer exports use the selected source
   only while the review is current; they do not expose internal reviewer metadata.

The command never sends email, generates a ZIP, grants processing consent or
confirms a customer summary. It uses the same exclusive state lock as Gmail.
An empty template is deliberately invalid. No missing database is initialized.
Approval is conservative evidence applicability, not a finding that someone has
lawful UK status or will obtain a visa.

Verification checkpoint: 32 tests passed in 0.74s across the new command,
underlying sponsor review and existing record-review command. Tests cover empty
templates, explicit review, ignored altered context, repeated/stale approval,
policy change, extra send fields, busy lock and nonexistent database. Lint and
mypy (100 source files) passed. Both command help entry points were exercised.
No real Gmail state was reviewed or changed. Guided nontechnical reviewer UI,
full-suite/CI and real final delivery remain unfinished.
