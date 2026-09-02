# ADR-001: bounded Agent and deterministic case workflow

- Status: accepted architecture; provider model selection pending live evaluation
- Date: 2026-09-02
- Scope: UK Standard Visitor document-preparation Demo

## Decision

Use one bounded extraction Agent and one optional bounded wording call inside a deterministic
workflow. Do not use a free-chat Agent, autonomous tool loop, multi-agent hand-off, or model-owned
case state.

The extraction call can only propose a `CasePatch`. The wording call can only turn an already-made
message plan into customer-facing prose. Neither call receives tools or authority to select the visa
route, decide eligibility, set requirements, clear issues, move lifecycle state, send a message,
generate a pack, or release a pack.

## Why this shape

The difficult part is dependable orchestration, not unconstrained reasoning. A multi-agent design
would add hand-off variance, latency, cost, and more failure points without creating an independent
task that needs separate authority. Requirements, consistency checks, lifecycle transitions,
idempotency, retries, delivery gating, and pack generation are predictable operations and belong in
code. Human judgment remains explicit for unsupported routes, serious history, unresolved
contradictions, uncertain evidence, and consequential final review.

## Workflow

```text
Email / WhatsApp event
        │ channel authentication, limits, idempotency, ordering
        ▼
typed InboundEvent
        │
        ▼
bounded extraction proposal ── schema + exact evidence excerpt
        │
        ▼
deterministic patch guard ── allow-list, type, grounding, confidence, conflict
        │ reject/abstain ───────────────► HUMAN_REVIEW_REQUIRED
        ▼
case workflow ── policy rules + evidence ledger + consistency checks
        │
        ▼
message plan ── blocked / awaiting confirmation / ready
        │
        ├── human-review state ─────────► deterministic safe wording
        └── optional wording model ─────► claim/length guard ─► deterministic fallback
        │
        ▼
transactional outbox ── finite retry + ambiguous-send reconciliation
        │
        ▼
deterministic eight-check delivery gate
        │ pass only after explicit applicant confirmation
        ▼
PDF / JSON / ZIP pack for human review
```

## Model selection

No model is selected by reputation. The live comparison begins with:

1. `gpt-5.6-luna` as the efficient/high-volume baseline.
2. `gpt-5.6-terra` as the balanced capability/cost challenger.

The current [OpenAI model guidance](https://developers.openai.com/api/docs/guides/latest-model)
describes Luna as the cost-sensitive/high-volume option and Terra as the balance of intelligence and
cost. Both candidates support the Responses API and Structured Outputs according to the current
[official model comparison](https://developers.openai.com/api/docs/models/compare). Availability is
still account-dependent and must be confirmed by the sandbox run.

Run every candidate at least three times on `evals/agent_cases.yaml`. A candidate is eligible only if
it passes every threshold in `evals/README.md`; quality and safety are gates, not weighted scores.
Among passing configurations, choose the lowest measured cost with acceptable p95 latency, then pin
an evaluated snapshot where the provider offers one. Re-run the same corpus before changing model,
prompt, schema, reasoning settings, SDK, or guard rules.

The model ID is deliberately mandatory in `OpenAIStructuredLLM`; there is no hidden default that can
change behaviour without a review decision.

## Stability controls

| Risk | Owner | Control |
|---|---|---|
| Invalid model shape | Agent boundary | Structured Outputs plus strict Pydantic schema |
| Invented fact | Patch guard | Exact inbound excerpt and field/type allow-list |
| Conflicting or uncertain fact | Patch guard | Reject candidates and move to human review |
| Prompt/document injection | Patch guard | Untrusted data has no state, tool, or send authority |
| Timeout/refusal/partial output | Agent boundary | At most two attempts, then zero-update abstention |
| Unsafe customer claim | Reply guard | Reject prohibited approval/submission guarantees |
| Wording model unavailable | Reply guard | Deterministic non-advisory template |
| Duplicate/delayed event | Workflow/storage | Provider ID idempotency, sender and ordering policy |
| Duplicate reply after crash | Outbox | Transactional claim and RFC Message-ID reconciliation |
| Premature pack | Delivery gate | Eight deterministic checks re-evaluated at download |

## Alternatives rejected

- **Free-form adviser chatbot:** cannot provide reproducible state, evidence, or delivery authority.
- **One autonomous Agent with tools:** places policy, state mutation, side effects, and release inside
  a probabilistic loop.
- **Multi-agent extraction/review/writer chain:** adds correlated model opinions, not independent
  legal or documentary verification; deterministic validators provide stronger separation.
- **Rules-only customer communication:** stable but too rigid for varied natural-language intake;
  retained as the safe fallback rather than the only interface.
- **Flagship model by default:** unmeasured cost and latency are unjustified for narrow extraction.
- **Cheapest model by default:** low cost cannot compensate for a failed safety or recall threshold.

## Consequences

The system can be more conservative than a human adviser and may send cases to manual review that a
person could resolve immediately. That is intentional for the Demo. Adding a new channel reuses the
same typed event, workflow, outbox, and gate; it does not create a new autonomous Agent. Live model
quality, Gmail delivery, WhatsApp delivery, and external usability remain separately labelled
experiments rather than implied by this architecture decision.
