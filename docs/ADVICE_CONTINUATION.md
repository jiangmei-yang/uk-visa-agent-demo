# Unfinished consultation and preparation obstacles — 2026-09-05

Subsequent [separate-email question repair](BATCHED_CONSULTATION.md) distinguishes
an actual unanswered applicant request from a delivered omission promise. An
unsent request can now be answered without pretending an earlier explanation was
sent; ambiguous sends still require reconciliation. The evidence below describes
the earlier, narrower continuation implementation.

This is an implemented, captured-transport repair, not a live-model naturalness
score or evidence that every ordinary conversation works. The original
[seven-item usefulness failures](ADVISER_USEFULNESS_REVIEW_2026-09-04.md) remain
visible. Gmail is still the primary test channel; no WhatsApp account is implied.

## What changed

- The three-answer limit now records which questions were omitted, together with
  the original request and its route/other qualifiers. A natural request such as
  “好，那接着讲刚才没说的吧” can pick them up after a database restart.
- Both the original omission notice and a later answer require an actual `SENT`
  outbox record containing the complete relevant text. Matching follows the
  renderer's case-insensitive contract; it does not ignore prices or qualifiers.
  Pending, failed, uncertain or superseded drafts do not count as answers.
- Pure information continuation needs no extraction-model call. A separate
  continuation question alongside new facts or attachments goes through normal
  extraction/document processing, then answers the consultation. A fresh
  independent FAQ takes precedence and leaves the older question pending.
- Answers are regenerated from the reviewed source set using the current
  evaluation date, not copied from an old mail. Expired guidance leaves the
  question open. This is **not** live web retrieval.
- Asking to continue an explanation does not resume paused preparation, confirm
  a summary, validate a document or authorize final delivery. Existing privacy,
  chronology, review and sender boundaries remain earlier in the workflow.
- Two bounded obstacle patterns now offer a concrete action: an employer cannot
  provide a letter; or dates remain undecided and the applicant asks what to do
  now. Existing files are not requested again. Missing background falls back to
  a necessary background question, not a claim that the route is unsuitable.

## Captured actual reviewed-sender examples

The extractor was replaced with source-grounded synthetic proposals, and network
access was disabled. Each quoted output went through `WorkflowService`, SQLite,
`AutomaticGmailReplySender`, and `OutboxDispatcher` to captured `SENT`. No real
mailbox or paid model was used. Evaluation was on 2026-09-05 Hong Kong time with
the reproducible source clock set to 2026-09-04.

First, a fictional customer asked where to apply, how early, the fee, and how to
translate Chinese documents. The reply answered application/timing/translation
and disclosed “这封还没有展开申请费用，可以接着逐项说明。” After reopening the DB:

Customer: “好，那接着讲刚才没说的吧。”

> 如果你申请的是 6 个月 Standard Visitor，GOV.UK 当前列出的签证申请费是 £135。额外购买的签证中心服务或加急服务不包含在这笔申请费内；付款时以官网显示为准。其他签证路线或有效期的费用不能直接套用这个数字。

The actual reply included the [official application page](https://www.gov.uk/standard-visitor/apply-standard-visitor-visa).
There were zero extraction calls on this pure continuation. A subsequent
“剩下的呢？” did not repeat the fee or request identity details. A separate
“我的姓名是示例安宁。好，那接着讲刚才没说的吧。” went through one normal extraction,
stored that name and answered the fee without starting a personal questionnaire.

Known employed, self-funded visitor context; customer:
“公司现在不给我开在职证明，我下一步可以先准备什么？”

> 公司目前不开在职证明，这个困难需要单独核实，不必为此停下所有准备。
> 适用路线还需确认；如果按普通访客路线准备，可以先从网银或银行索取正式银行对账单，核对可用资金及其来源，再和目前估计的旅行支出放在一起检查。
> 这些材料不能自动替代在职证明；取得困难和现有证据需要顾问另行核对，在职情况这项检查仍保留。

The reply linked the [supporting-document guidance](https://www.gov.uk/government/publications/visitor-visa-guide-to-supporting-documents/guide-to-supporting-documents-visiting-the-uk#demonstrating-personal-circumstances).
For a known student asking “日期还是定不下来，我现在能先准备什么？”, the captured
reply suggested obtaining the school's current-enrolment letter, keeping leave
arrangements undecided until known, and retained the planned-date and passport
validity checks. It did not ask for a name or invent dates.

## Retention and limitations

`Case.pending_advice` is private case state, not model training or another agent's
memory. It retains the full latest original question context to avoid stripping
conditions, including personal details supplied in that context. The case also
retains its latest customer message and outbound replies. Export and README now
describe this accurately; the previous “only bounded excerpts” statement was not
accurate for this state. No raw MIME archive is introduced by this change.

An answered item is removed on the next successfully processed event after
verified sending. There is no automatic TTL or size/count cap for unanswered
context, including abandoned unsent promises. Case export includes it and case
deletion removes it from the active DB. Neither action erases remote mail,
backups or physical SQLite remnants. A bounded-retention design remains work.

The continuation matcher supports explicit current requests, not every possible
paraphrase. Mixed clauses still require safe scope; conditional, quoted and
reported requests do not become instructions. The obstacle selector covers the
two patterns above, not all unavailable evidence, individual document acceptance
or route decisions. A real applicant consent trial, uncoached recipient trial
and broader provider evaluation are still outstanding.

## Reproduce

```sh
.venv/bin/pytest -o addopts='' -q \
  tests/integration/test_advice_continuation.py \
  tests/integration/test_advice_memory_privacy.py \
  tests/unit/test_advice_continuation_intent.py \
  tests/unit/test_preparation_obstacles.py \
  tests/integration/test_adviser_question_pacing.py
```

Failures observed and retained during development: 23/30 initial continuation
checks failed on the Chinese “好，那” prefix; the lowercase guarded-SENT variant
then exposed a case-sensitive match; 14 mixed fact/file continuation checks
showed extraction succeeded but the question was lost. The first full suite also
had six old pacing failures because missing background was treated as a route
review. These were repaired without relaxing send, consent or old pacing tests.
