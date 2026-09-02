# Accuracy scorecard

Accuracy and stability are separate release gates. A deterministic workflow can pass every local
test while an external model still fails extraction, and a model can extract facts correctly while
an unsafe workflow releases an incomplete pack. This repository reports both layers separately.

## Current result (2026-09-03)

| Layer | Evidence | Result |
|---|---|---:|
| Workflow, policy gates, confirmation and adversarial controls | `make accuracy` | 23/23 passed (100%) |
| Complete repository regression | `make test` | 90/90 passed (100%) |
| Clean-run deterministic delivery | `make stability` | 20/20 identical packs (100%) |
| Concurrent review reads | `make stability` | 100/100 successful (100%) |
| Live DeepSeek extraction on visa corpus | `make agent-eval-deepseek` | Not scored: no local visa-project key |
| Live OpenAI extraction on visa corpus | `make agent-eval-live` | Not scored: no local visa-project key |

The local workflow score is full marks for the committed automated scope. The product does **not**
claim an overall AI-accuracy score until a candidate model passes the 15-case corpus at least three
times (45 runs) and meets every threshold in `evals/README.md`.

## Corrections made during the accuracy audit

- Missing criminal, civil, refusal and immigration history is now unknown, not silently `false`.
- Official application facts now block delivery when missing or lacking provenance: nationality and
  application country, travel dates, accommodation, trip cost, address and conditional income.
- Travel dates must be future-facing, ordered and no longer than six calendar months.
- British citizenship or UK right-of-abode wording is held for human route review.
- Model canonical values now match policy values for family visits and employer/school funding.
- Every non-English/Welsh document needs its own linked certified translation.
- Personal sponsorship requires separate support, funds and relationship evidence, plus UK status
  evidence when the sponsor is in the UK.
- Conflicting active evidence creates a blocker instead of silently overwriting an earlier source.
- Profile and final confirmations must be standalone bounded statements; Chinese confirmations are
  supported and negations/instructions do not count.

## Official basis and claim boundary

The policy snapshot was reverified on 2026-09-03 against:

- https://www.gov.uk/standard-visitor/apply-standard-visitor-visa
- https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk

GOV.UK states that supporting documents do not guarantee success. This Demo prepares a
source-linked review pack; it does not decide eligibility, predict approval, submit an application,
perform production OCR or detect document fraud.
