.PHONY: install lint format typecheck test test-all backfill seed seed-reset check clean

install:
	uv sync

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest -m "not slow"

# includes the 100k event search bench
test-all:
	uv run pytest

backfill:
	uv run python -m app.search.backfill

# Realistic demo data, sent through the real ingest path
seed:
	docker compose exec app python -m app.seed

seed-reset:
	docker compose exec app python -m app.seed --reset

check: lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +