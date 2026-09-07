# Mixed emails and deferred location answers

This checkpoint corrects two ordinary customer interactions, without weakening
the final applicability review or fabricating a legacy location boolean.

1. A separate question no longer invalidates an explicit sponsor statement in
   the same email. Question punctuation stays with its sentence; declarative-
   looking questions, conditions and same-clause uncertainty remain rejected.
   The existing conservative rejection of quoted material is unchanged.
2. Supported whole uncertainty replies to a verified SENT dimension-specific
   question record a source/question/dimension-bound deferral. They neither
   imply a negative location fact nor immediately repeat the question. A later
   explicit answer to the missing dimension clears the active deferral while
   preserving history. Repeating the already-known dimension does not clear it.
   Sponsor replacement retires active location deferral for the former identity.

Verification: 118 tests passed in 1.20s across parsing, mixed bilingual workflow,
short-answer context rejection, uncertainty then later answer, persistence and
review. Lint and mypy (99 source files) passed. No live mailbox operation, real
applicant review or provider call was made. Full-suite and fresh provider
evidence remain pending; this checkpoint is not a release claim.

Remaining delivery work includes the changed seven-turn sponsor dialogue,
source-bound resolution of conflicting historical statements, reviewer UX,
customer summaries/PDF presentation and actual authorized Gmail final delivery.
