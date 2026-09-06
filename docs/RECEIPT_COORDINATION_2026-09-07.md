# Receipt coordination and partial-date precision

The retained v9 English reply joined passport name and birthday with a comma
instead of a conjunction. The bounded receipt now uses natural English list
coordination for one, two or more acknowledged fields. No extra personal data is
echoed or synthesized.

Inspection also found that either one-sided travel date produced `your updated
travel dates`, regardless of whether both dates were supplied or changed. English
and Chinese receipts now distinguish arrival-only, departure-only and both dates.
The generic receipt says planned dates, not an unproven correction. Existing
correction and confirmation workflows keep their separate authority.

## Verification

- Ten new receipt cases cover empty, single, two and three items, English and
  Chinese one-sided dates, state immutability and absence of confirmation grants.
  The initial run exposed five old-output failures and one invalid test-fixture
  type (budget must be a string in `latest_received_facts`); that fixture was
  corrected rather than weakening the domain type.
- Related receipt/question/preparation tests: **80 passed in 0.27 seconds**.
- Automatic Gmail reply, durable pacing, birthday and editorial integrations:
  **94 passed in 3.25 seconds**, before the additional replay below.
- Two original v9 provider responses were replayed through the captured sending
  path. The actual reply contains the conjunction, while name/birthday remain
  equal to the historical profile and no confirmation/delivery is granted.
  This is offline replay, not new model or Gmail evidence. Updated editorial plus
  new unit module: **17 passed in 0.48 seconds**. Network is denied in that replay.
- Lint and configured typing passed after correcting a local variable-name
  collision with the Chinese branch.

The two existing full-source report binding tests correctly reject the changed
conversation module. Historical v9/v18 reports and previous CI are unchanged and
are not represented as covering this new runtime. No full-suite/new live-provider
acceptance or Gmail deployment is claimed for this edit. Broader ordinary-language
and independent recipient evaluation remain outstanding.
