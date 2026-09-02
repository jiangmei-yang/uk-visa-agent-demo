.PHONY: setup demo test lint typecheck stability agent-eval-live agent-eval-deepseek web webhook start stop clean

setup:
	uv sync --extra dev

demo:
	uv run visa-agent demo --reset

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy

stability:
	uv run python scripts/stability_check.py

agent-eval-live:
	uv run python scripts/agent_eval.py --model "$${MODEL:?Set MODEL to an evaluated model ID}"

agent-eval-deepseek:
	uv run python scripts/agent_eval.py --provider deepseek --model "$${MODEL:-deepseek-v4-flash}"

web:
	uv run visa-agent web

webhook:
	uv run visa-agent webhook-server

start:
	docker compose up --build --detach

stop:
	docker compose down

clean:
	rm -rf demo_output data .pytest_cache .mypy_cache .ruff_cache
