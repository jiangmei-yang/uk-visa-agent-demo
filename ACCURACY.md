# Accuracy scorecard

Accuracy and stability are separate release gates. A deterministic workflow can pass every local
test while an external model still fails extraction, and a model can extract facts correctly while
an unsafe workflow releases an incomplete pack. This repository reports both layers separately.

## Latest measured result (2026-09-04, ordinary Gmail hardening)

- Local suite: 214 tests; lint and strict typing pass. Deterministic stability: 20 identical
  complete ZIP runs and 100 successful concurrent reads.
- Ordinary four-PDF/OCR intake plus natural date correction: 3 repeated journeys,
  48/48 checks (`eval_output/natural_journey_2026-09-04-v4.json`). The same two-turn
  scenario was exercised through real Gmail, with both reviewed replies visibly received.
  Both Gmail replies used safe fallback wording; the identity-summary blocker correctly
  prevented a final delivery. This is not a complete ordinary-document-to-pack test.
- The first broad extraction regression missed a name (all-field recall 97.5%). After that
  correction, a 45-run regression exposed missing explicit negative facts (95% recall).
  Both failed reports are preserved. After negative-fact instructions, the same 15-case,
  3-repeat matrix reached 100% on its extraction and safety metrics in
  `eval_output/agent_regression_2026-09-04-v3.json`.
- A separate new negative-language matrix then exposed omitted Chinese route denial
  (`eval_output/negative_facts_2026-09-04.json`, 83.33% field recall, safety decisions passed).
  Passing the earlier matrix was therefore **not** proof of general accuracy. See the
  subsequent reports and live evidence log for follow-up results. Further iterations exposed
  a relationship incorrectly used as sponsor name, then an omitted funding source; these failed
  reports are also retained. Code now rejects relationship-only names. After corrections,
  `eval_output/negative_facts_2026-09-04-v4.json` passed all 15 runs and its strict corpus gate.
- The evaluator now records a prompt SHA-256 and explicit release-gate result; `--strict` exits
  nonzero on failure after preserving the report. Missing metrics also fail this gate.
- The updated extractor passed all 75 formatting/quoted-history/injection inputs in
  `eval_output/agent_stress_2026-09-04-v3.json`, and all 10 targeted repetitions of the
  multilingual sponsor-injection input in `eval_output/sponsor_injection_2026-09-04.json`.
  The 75-input run repeats each surface once: its consistency metric is not repeatability
  evidence. The separate targeted run supplies repeatability evidence only for that one case.
- An intermediate extractor snapshot's larger 225-run stress test is retained separately in
  `eval_output/agent_stress_2026-09-04-v2.json`: all-field recall 99.33%, critical recall 99.30%,
  repeat consistency 98.67%, with no unsupported accepted facts or unsafe boundary proposals.
  It missed four sponsor facts in one multilingual-injection repetition. The newer 75-input
  and 10-repeat results above do not retroactively turn that larger failed run into a pass.

All of these are internal synthetic evaluations. They are not an external usability study,
an applicant outcome study, a legal accuracy guarantee or a universal “full marks” score.

Separately, a real Gmail GET with deliberately invalid credentials returned 401
(`eval_output/gmail_invalid_auth_2026-09-04.json`). This is a narrow external rejection check,
not a revocation/recovery experiment. Local send fault injection now verifies timeout/5xx
uncertainty is reconciled before any resend; these simulated failures are not live outages.

The Twilio adapter now likewise withholds automatic resend after uncertain transport/5xx outcomes.
Its missing automatic correlation capability is explicit rather than represented as a successful
negative search. Contract and dispatcher fault-injection tests pass; no live Twilio exchange or
automatic lost-SID recovery is claimed. See `WHATSAPP_SANDBOX.md` for the remaining operator step.

## Earlier baseline (2026-09-04; retained, not the current release score)

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
