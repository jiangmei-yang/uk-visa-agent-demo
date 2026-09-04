# Start here — interviewer walkthrough

This repository is a self-contained assessment Demo. It uses synthetic applicant data and does not
need an OpenAI key, Gmail login, or internet access after the first Docker build.

## Start the Demo

1. Install and open [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. Download this GitHub repository as a ZIP and extract it.
3. On macOS, double-click `START_DEMO.command`. On Windows, double-click
   `START_DEMO_WINDOWS.bat`.
4. Wait for the browser to open the review console. The first launch may take a few minutes.

If macOS blocks the launcher, right-click it, choose **Open**, then confirm **Open**. Keep Docker
Desktop running while reviewing the Demo.

## What to inspect

The featured case begins with two blockers: conflicting trip dates and a missing certified
translation. Follow-up evidence resolves those issues, but delivery remains blocked until the
applicant explicitly confirms the final summary. The final status is **Ready for human review**.

In the console:

1. choose **Try the workflow** and send the three prepared applicant messages;
2. see the first pack withheld for two named evidence problems;
3. see the corrections clear those blockers while the pack still waits for confirmation;
4. check the displayed summary, confirm it, and download the released pack;
5. return to the finished case to inspect the evidence ledger, superseded document, and rule results.

The guided test is synthetic and deterministic, so it needs no API key and gives every interviewer
the same auditable result. The repository separately includes a real DeepSeek full-workflow report;
that live provider is not silently used by the credential-free page.

The language model cannot clear blockers or authorise delivery. Those decisions are made by
versioned rules, an allow-listed workflow, and an explicit confirmation gate.

## Stop or restart

- Double-click `STOP_DEMO.command` on macOS or `STOP_DEMO_WINDOWS.bat` on Windows.
- Starting again preserves existing cases in the Docker volume; it does not reset customer data.
- The credential-free synthetic workflow produces byte-identical packs for the same fixture input.
- The finished-case page can export its local synthetic case data. Deleting a case requires a clear
  confirmation. Do not use deletion or remove the Docker volume as a troubleshooting shortcut.

## What this does not demonstrate by itself

The local guided page is an offline fixture replay, not a connected inbox. Gmail has a separate
registered-sender service, and WhatsApp still requires account/device setup and real exchanges.
Neither a running console nor a green automated-test result proves those integrations work.
See `GMAIL_AUTOMATIC_SERVICE.md` and `WHATSAPP_SANDBOX.md` for the distinct operating modes.

An operator with the Python environment installed can run
`uv run python scripts/check_live_setup.py` to see missing local configuration without contacting
a provider or printing secret values. It does not log in, validate credentials, send messages or
prove delivery. An `.env` file alone is not automatically loaded by the worker.

## If the browser does not open

Open <http://127.0.0.1:8000> manually. If it still does not load, confirm Docker Desktop is running,
then double-click the start file again. The launcher prints a clear error and the recent container
logs if startup fails.

This is document-preparation assessment software, not legal advice, an eligibility decision, or a
visa-submission service.
