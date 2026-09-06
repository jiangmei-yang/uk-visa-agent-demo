# Isolated container acceptance: synthetic lab only

Source under test: `dac9169` on `codex/application-records`, not deployed main.
Docker engine 29.5.2 built the existing Dockerfile with the frozen dependency lock.
Image: `sha256:5a3652c4110e187aee4b2a5a210fea9fab3a2e7d90acef51ac4e8d679e5797f1`.
The build used cached base layers but a fresh project/dependency installation;
it was not an offline first-ever Docker download test. Docker emitted a legacy
builder deprecation warning; the build completed successfully.

## Executed checks

A separate container `visa-agent-acceptance-dac9169` ran as `appuser`, with no
host state mount and no Gmail credentials or model key. It used a random loopback
port, leaving the existing 8000 service untouched. Startup populated the fresh
database and reached Docker's healthy status.

`scripts/container_acceptance_probe.py` exercised actual HTTP requests against
that instance. The retained report is
`eval_output/container_acceptance_2026-09-07-dac9169.json`: **12/12 checks passed**.
It checks home/lab availability, initial withheld download, date/translation
blockers, rejection of out-of-order final confirmation, correction without
premature delivery, explicit fixture confirmation, ZIP CRC, seven expected pack
artifacts and supporting documents. The probe requires an explicit isolated
loopback port other than 8000 and an explicit synthetic-state-change flag. Port
validation is a guardrail, not proof that any caller-selected instance is safe.
Ten target-validation tests pass (0.02s); Ruff passes. No full suite was run for
this standalone probe addition.

After restarting **only this isolated container**, the lab retained three
processed steps and the exact same downloadable archive:
`409771edeb3adaebce31a39318fcb779509ca43b8abf5bd44bf25d4fde4ea537`.
The first post-restart request hit the old dynamic port and was refused. Docker
inspection confirmed healthy state with the new port, 32769 instead of 32768;
querying that port succeeded. No second restart or state reset was used to make
the check pass. Production Compose uses its explicit 8000 binding, not this
test's dynamic-port mapping.

The isolated container was then stopped, not deleted; its synthetic state and
the acceptance image remain available. No production service was stopped,
restarted, overwritten or upgraded.

## Reproduction

Build a separate tag from the desired source, then create a disposable container:

```sh
docker build -t visa-agent-acceptance:local .
docker run -d --name visa-agent-acceptance-local -p 127.0.0.1::8000 \
  -e VISA_AGENT_DATABASE=/app/runtime/data/visa_agent.db \
  -e VISA_AGENT_OUTPUT_DIR=/app/runtime/demo_output visa-agent-acceptance:local
docker port visa-agent-acceptance-local
```

After it is healthy, substitute its actual port and a new report filename:

```sh
python scripts/container_acceptance_probe.py --base-url http://127.0.0.1:PORT \
  --allow-synthetic-state-changes --output eval_output/container-acceptance-new.json
```

The probe resets that instance's synthetic lab. Never aim it at an instance
someone is using. It does not reset the main case, inspect Gmail or send email.
For restart checking, re-read `docker port` rather than assuming the dynamic
binding is stable. Stop the isolated instance when finished.

## What this does not prove

This is a deterministic labelled-fixture journey, not unrestricted document
understanding, live DeepSeek/Gmail acceptance, independent UX evaluation or a
visual audit of every generated PDF. It does not exercise Windows double-click
installation, macOS security prompts, first-time Docker installation, Compose's
named-volume recreation, or the unfinished nontechnical operator review UI.
The project therefore still cannot be described as fully accepted or perfect.
