# Sponsor location short replies — implementation evidence

Short yes/no replies now have a bounded context path independent of model
extraction. The workflow checks the latest SENT outbox row, recipient, thread,
send time, current question-event ledger, exact dimension-specific question text
and the source/identity binding saved when that question was planned. Planning
alone is not evidence of sending. A stale identity epoch cannot reuse a question.

Only supported whole short answers are parsed. The resulting typed observation
retains the literal answer, inbound event and contextual question event ID.
The normal owner, consent, event-order and finalization boundaries remain in
front of this path. A supplied dimension is never converted into the historical
conflated boolean. Completing the second dimension still invokes the existing
review requirement and cannot confirm or send a final pack.

28 fictional workflow tests cover English/Chinese yes/no and SENT, PENDING,
wrong recipient, wrong thread, late send, changed identity epoch and unrelated
payload. All passed. The combined location parser, storage, review, partial and
short-answer regression was 87 passed in 0.97s. Lint and mypy (99 source files)
passed. These tests use local state and stub extraction, not real Gmail or a
fresh model evaluation.

Remaining: uncertain short answers need explicit dimension-bound deferral;
mixed factual statements and separate questions still hit the parser's coarse
question-mark rejection. The seven-turn sponsor journey, fresh model evidence,
operator UX and final rendered summaries remain pending. This checkpoint was
not deployed to the real Gmail worker and is not a full-suite/release claim.
