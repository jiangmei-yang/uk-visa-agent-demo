# Partial sponsor location intake checkpoint

The previous checkpoint sent even one unambiguous location dimension to human
review. This interrupted ordinary intake. The workflow now persists the supplied
dimension and stays in draft when the other dimension is simply missing. It
does not clear an unrelated existing hold. Contradictory values still require
review, and the independent applicability release gate remains false until a
current review exists.

The question renderer distinguishes missing residence from missing current
physical presence. It acknowledges the supplied dimension without asking it
again. Four fictional English/Chinese workflows cover both arrival orders of
the two dimensions, closing/reopening SQLite between emails, exact event source
retention, and transfer to review after the second explicit statement. The
combined parser/storage/workflow/review run passed 59 tests in 0.59s. Lint and
mypy (99 source files) passed.

This is not completion of natural sponsor intake: contextual short answers
(“No”, “不在”) still need binding to the actual SENT dimension-specific question,
and mixed statements plus questions currently encounter the parser's coarse
question-mark rejection. The historical seven-turn sponsor replay expects the
old conflated boolean and old question order; it has not been relabelled as
passing or used as fresh provider evidence. A source-safe conversational
continuation and new model-backed journey remain required. No full-suite green
claim, Gmail restart, real operator approval or external send occurred.
