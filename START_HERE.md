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

In the console, inspect:

1. the current case status and deterministic delivery decision;
2. the evidence ledger and superseded document;
3. the rule results and resolved issues;
4. the generated application-pack download.

The language model cannot clear blockers or authorise delivery. Those decisions are made by
versioned rules, an allow-listed workflow, and an explicit confirmation gate.

## Stop or restart

- Double-click `STOP_DEMO.command` on macOS or `STOP_DEMO_WINDOWS.bat` on Windows.
- Starting again recreates the same case and produces byte-identical output.

## If the browser does not open

Open <http://127.0.0.1:8000> manually. If it still does not load, confirm Docker Desktop is running,
then double-click the start file again. The launcher prints a clear error and the recent container
logs if startup fails.

This is document-preparation assessment software, not legal advice, an eligibility decision, or a
visa-submission service.
