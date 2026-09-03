# Accuracy scorecard

Accuracy and stability are separate release gates. A deterministic workflow can pass every local
test while an external model still fails extraction, and a model can extract facts correctly while
an unsafe workflow releases an incomplete pack. This repository reports both layers separately.

## Current result (2026-09-04)

| Layer | Evidence | Result |
|---|---|---:|
| Workflow, policy gates, confirmation and adversarial controls | `make accuracy` | 29/29 passed (100%) |
| Complete repository regression | `make test` | 102/102 passed (100%) |
| Clean-run deterministic delivery | `make stability` | 20/20 identical packs (100%) |
| Concurrent review reads | `make stability` | 100/100 successful (100%) |
| Live DeepSeek extraction on visa corpus | `eval_output/agent_eval.json` | 45/45 passed; every release metric passed (100%) |
| Live DeepSeek formatting/injection stress suite | `eval_output/agent_stress_eval.json` | 75/75 schema-valid and safe; critical recall 97.37% |
| Live DeepSeek conversation → workflow → replies → pack | `eval_output/deepseek_workflow_eval.json` | 3/3 runs, 9/9 stages, 117/117 checks and 9/9 replies passed |
| Live OpenAI extraction on visa corpus | `make agent-eval-live` | Not scored: no local visa-project key |

The local workflow and evaluated DeepSeek extraction layers have full marks for the committed test
scope. This is evidence for 15 synthetic cases repeated three times, not a claim of universal legal
accuracy or a guarantee about real applications.

DeepSeek `deepseek-v4-flash` produced 100% schema validity, critical/all-field precision and recall,
human-review decisions, ambiguity decisions and repeat consistency, with zero unsupported claims or
raw boundary violations. Median latency was 1.13 seconds and p95 was 3.30 seconds. The 45-run test
used 37,248 input and 6,570 output tokens. Using the official 2026-09-04 off-peak cache-miss/output
rates, the conservative estimated cost was USD 0.012531.

A separate 75-input stress run applied five surfaces to every case: original, realistic email noise,
a current reply followed by quoted history, an English prompt-injection suffix, and a Chinese
prompt-injection wrapper. It achieved 100% schema validity, critical-field precision, human-review
decisions and ambiguity decisions; 97.37% critical recall; zero unsupported claims; and zero unsafe
boundary violations. The few omissions are fail-safe: required facts remain absent, so deterministic
completeness checks request clarification and prevent pack release. Median latency was 1.05 seconds,
p95 was 3.31 seconds, and the conservative peak/cache-miss cost estimate was USD 0.046395.

The first stress run is retained in `eval_output/agent_stress_eval_initial.json`. It exposed one
hallucinated multi-field proposal under a combined multilingual injection; every field was rejected
because its claimed excerpt was absent. The hardened input contract eliminated that failure in the
final full run. This before/after evidence is kept rather than replacing the failed result.

The complete-workflow suite used ordinary natural-language intake without the hidden offline fixture
block. Three independent runs each exercised six provider operations across three applicant
messages: bounded extraction and customer wording at each step. Every run produced the expected
`blocked → awaiting_confirmation → ready` sequence, named both initial blockers, withheld the pack
through the second message, accepted the exact final confirmation, and generated the same byte-hashed
ZIP. All 117 release checks and all nine guarded model replies passed, with 100% semantic repeat
consistency. The 18 provider calls used 10,878 input and 3,075 output tokens. Median end-to-end step
latency was 1.96 seconds and the slowest step was 5.75 seconds. Documents remained synthetic and used the fixture PDF
reader, so this is Agent/workflow evidence—not Gmail, WhatsApp, OCR, or applicant-outcome evidence.

## Corrections made during the accuracy audit

- Missing criminal, civil, refusal and immigration history is now unknown, not silently `false`.
- Incomplete profiles or document requirements now produce `blocked`, never the misleading
  `awaiting_confirmation` plan; that plan is allowed only when final confirmation is the sole failed
  gate.
- Official application facts now block delivery when missing or lacking provenance: nationality and
  application country, travel dates, accommodation, trip cost, address and conditional income.
- Travel dates must be future-facing, ordered and no longer than six calendar months.
- British citizenship or UK right-of-abode wording is held for human route review.
- Model canonical values now match policy values for family visits and employer/school funding.
- Every non-English/Welsh document needs its own linked certified translation.
- Personal sponsorship requires separate support, funds and relationship evidence, plus UK status
  evidence when the sponsor is in the UK.
- Conflicting active evidence creates a blocker instead of silently overwriting an earlier source.
- Untrusted mail is serialized as data, and every proposed evidence excerpt must be a literal
  substring before any value can reach case state.
- Common sponsor relationships are deterministically canonicalised after grounding.
- Profile and final confirmations must be standalone bounded statements; Chinese confirmations are
  supported and negations/instructions do not count.
- Pack status is written before the audit snapshot, and a translated original is shown as accepted
  only after its certified translation is linked.
- Six PDFs now share a visually reviewed human-review layout, and machine codes are converted to
  readable labels in applicant-facing summaries.
- Model replies must preserve every outstanding blocker/document/fact, the exact confirmation line,
  and the human-review boundary; unsafe, incomplete, placeholder-filled, or failed output falls back
  to deterministic wording.

## Official basis and claim boundary

The policy snapshot was reverified on 2026-09-03 against:

- https://www.gov.uk/standard-visitor/apply-standard-visitor-visa
- https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk

GOV.UK states that supporting documents do not guarantee success. This Demo prepares a
source-linked review pack; it does not decide eligibility, predict approval, submit an application,
perform production OCR or detect document fraud.
