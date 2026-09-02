.PHONY: setup demo test lint typecheck stability web start stop clean

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

web:
	uv run visa-agent web

start:
	docker compose up --build --detach

stop:
	docker compose down

clean:
	rm -rf demo_output data .pytest_cache .mypy_cache .ruff_cache
