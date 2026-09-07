# Gmail web public-consultation validation

This is an operator-driven development conversation using authorized mailboxes,
not independent user evaluation or completed document-pack delivery.

The previous ordinary inquiry sent from Outlook was found in the service's Gmail
web inbox, with a real outbound reply. That reply unnecessarily asked whether the
visit was tourism after the inbound message already said tourism. Public enquiry
handling now preserves an explicit holiday purpose within the full-matched enquiry;
it does not infer purpose from an arbitrary keyword, quote or negation.

A separate new conversation was then sent using Gmail web under an isolated
registered-sender service and a fresh activation cutoff. The email subject and
body contained no test marker or special command. The client's Gmail inbox showed
the actual reply, containing preparation guidance and no repeated purpose question.

The next ordinary request was “先给我一个官方申请入口就好，其他的之后再说。”
The client's Gmail inbox showed a reply with the correct application URL but an
unrequested process explanation. The link-only preference matcher now accepts the
leading “先” without changing authority, policy or data-processing gates. A new
customer correction was sent to verify the repaired live path; recipient-side
verification of that correction is still pending at this checkpoint.

Targeted tests: 25 passing public/Gmail tests; 31 passing public/preference tests
(overlapping selections). Ruff and mypy pass. Older provider reports remain genuine
historical reports but must be refreshed before calling them current-source after
these changes. No personal-data consent, application confirmation, human review or
final ZIP delivery is claimed here.

## Main branch CI checkpoint

[Run 34085081041](https://github.com/jiangmei-yang/uk-visa-agent-demo/actions/runs/34085081041)
on `6206823` completed successfully: 5,420 tests passed, six platform-specific
launcher tests skipped on Linux, one dependency warning; five clean stability runs
and 100/100 concurrent reads passed. Docker startup, health, ZIP and delivery-gate
smoke checks passed. This CI predates the two live-conversation repairs above.
