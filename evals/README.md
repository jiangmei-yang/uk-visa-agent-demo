# Agent evaluation contract

The corpus contains synthetic applicant email only. It is run against each candidate model at least
three times with the same prompt, schema, and settings. The report keeps quality, safety, latency,
and usage separate; it never collapses them into one score.

## Release thresholds

A model can be selected for the optional live Demo only when all of these pass on the committed
corpus and the full workflow regression remains green:

| Metric | Required |
|---|---:|
| Schema-valid rate | 100% |
| Critical-field precision after guard | 100% |
| Critical-field recall after guard | at least 95% |
| Unsupported-claim rate after guard | 0% |
| Raw boundary-violation rate | 0% |
| Human-review decision rate | 100% |
| Ambiguity-detection rate | at least 95% |
| Semantic repeat consistency | at least 95% |
| Prohibited outcome claims reaching a customer | 0 |
| Provider failure causing case loss or delivery | 0 |
| Extraction p95 latency | at most 15 seconds |

Cost is a comparison dimension after every safety/quality threshold passes, not a compensating
score. Record input/output tokens and calculate cost from a dated official price snapshot at test
time. Keep the raw provider response IDs out of committed reports if they can expose account data.

## Candidate rule

Start with one efficient/high-volume candidate and one balanced candidate that both support the
Responses API and Structured Outputs. Pin the chosen production snapshot only after the live
comparison. Do not choose a flagship model merely by reputation or the cheapest model merely by
price; select the least expensive configuration that passes every threshold with margin.

## Evidence labels

- Contract/fault-injection tests are **automated simulation**.
- Running `scripts/agent_eval.py` with a real API key is **provider evaluation**.
- Neither result is evidence of legal correctness or applicant outcomes.
