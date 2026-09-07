# macOS launcher fault-injection checks

The actual `START_DEMO.command` was executed with `zsh -f` in a temporary directory.
An isolated command path supplied fake Docker, curl, browser-open and sleep commands;
it could not fall through to the real tools. No containers, mailboxes, models or
applicant data were accessed. Sleep was simulated, not a real three-minute wait.

Six cases passed: Docker absent; daemon stopped; build failure; 90 unsuccessful
health attempts followed by diagnostics; healthy startup; browser-open failure.
Every case checks that no destructive down/prune/volume-removal command was issued.
The browser failure now gives the manual URL and waits for Return without stopping
the healthy service. Previously it ignored the browser command's failure.

Validation:

```
.venv/bin/python -m pytest -o addopts='' -q tests/unit/test_macos_launcher_failures.py tests/unit/test_release_smoke.py tests/unit/test_startup.py
# 21 passed in 5.19s
.venv/bin/ruff check tests/unit/test_macos_launcher_failures.py
# All checks passed
zsh -n START_DEMO.command
# exit 0
git diff --check
# exit 0
```

These are local launcher control-flow tests, not a fresh Docker install, native
Windows execution or uncoached human usability study. On hosts without zsh the
six native-launcher tests explicitly skip; a skipped run must not be cited as
macOS validation. The preceding ab0e69e CI evidence predates this launcher change.
