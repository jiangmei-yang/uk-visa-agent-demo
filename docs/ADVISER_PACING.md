# Adviser pacing and residential-detail repair

2026-09-04. This is exposed-defect repair with isolated automated evidence, not a
new blind evaluation, a human-likeness score, or a real mailbox delivery test.

## What was wrong

The first cold-start conversations retained two defects outside the headline
checks: a FAQ accompanied by a funding answer could trigger unanswered
accommodation questions plus new income questions; country-only residence could
pass a non-empty `current_address` check. The original provider reports and their
failures remain unchanged in [the experiment record](COLD_START_CONVERSATIONS.md).

## Conversation contract

- Answer a current FAQ or an available requested checklist without appending an
  intake questionnaire merely because the same email supplies a fact. Keep the
  fact, evidence and outstanding questions on file.
- An independent next-step request still advances one item. A plain answer to an
  intake question can continue preparation. A checklist question that cannot yet
  produce an answer may ask for the missing context; silence is not success.
- Pause and confirmation boundaries remain separate. A quiet resume such as
  “that's all for this email” is a receipt, not permission to append new questions.
- Only questions associated with an actually SENT reply, still incomplete and
  not deferred, can provide context for an ambiguous short answer. An unsent draft
  is not conversation history. A FAQ does not erase previously sent pending items.
- A same-valued city-only reply to a sent address question is not silence: explain
  which residential details are missing rather than sending a generic waiting
  receipt. A simultaneous FAQ still takes precedence over intake.
- Missing-detail pacing does not remove document issues, permit unsupported
  advice, grant processing/summary consent, or authorize final-pack dispatch.

The new sent-aware integration suite starts with an empty case, reconstructs
SQLite/workflow/model instances on every turn, passes through the real automatic
Gmail sender and dispatcher with a local capture adapter, and verifies SENT and
duplicate-event invariants. Network connections are prohibited. It is **not** a
Google API exchange or a test of a real model's extraction accuracy.

The first nine pacing tests reproduced five failures before the repair. The final
suite has 15 sent-aware pacing/context tests and 59 residential-detail tests. The
additional address journey reaches a fresh profile-summary request after a complete
address, without granting confirmation or sending a pack. These are new synthetic
regressions, not a revised score for the exposed cold-start provider conversations.

One actual captured Chinese reply in this isolated test acknowledges self-funding,
explains that statements help show ownership, source, movements and accessibility
of funds in relation to trip costs, and links to the reviewed GOV.UK supporting
documents page. Its planned intake questions are empty. This reading establishes
the intended answer/pacing combination for that example, not a tone score.

## Residential detail is not address verification

One shared completeness predicate now drives the delivery gate, next questions
and pending-question ledger. A supplied country or city remains useful context
with its source evidence but cannot count as a completed home address. An explicit
partial correction replaces an outdated complete address and invalidates prior
confirmation; it does not preserve a known-wrong address to pass the gate.

The check looks for minimum residential detail. It does not require a universal
postcode or house number, and includes named rural homes, dormitories and selected
non-Latin formats. It is not geocoding, proof of ownership, postal deliverability,
or universal international address validation. Unrecognized/incomplete formats
remain ordinary missing details, not automatic human-review cases. Proposed
details still have to be grounded in the customer's excerpt.

Seven older “complete profile” test fixtures used placeholders such as “Fictional
campus”. Those fixtures now contain an explicitly fictional, structurally detailed
address; their assertions were not weakened. The preparation-control probe seed
also changes for future synthetic runs. Historical reports retain their original
seed: a run with the new seed is not an exact replay of the old input.

## Remaining acceptance

The reply system still uses bounded, reviewed advice. This repair does not prove
coverage of arbitrary questions, consistently natural correction wording or
independent customer satisfaction. An authorized uncoached participant, consented
ordinary materials and recipient-side final ZIP delivery remain required. No new
provider calls, recipient emails or material uploads are part of this repair.
