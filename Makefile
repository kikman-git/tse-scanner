.PHONY: install sync test lint scan ingest universe backtest research clean

install sync:
	uv sync --extra dev --extra api

test:
	uv run pytest -q

lint:
	uv run ruff check src tests

# Pipeline shortcuts (DESIGN.md §9) — subcommands are Phase-staged stubs until built.
# --refresh: without it the cached weekly margin PDF in data/raw/ is re-parsed forever and
# re-ingests silently never pick up the new Tue/Wed print.
ingest:
	uv run tse-scanner ingest --refresh

universe:
	uv run tse-scanner universe

scan:
	uv run tse-scanner scan

backtest:
	uv run tse-scanner backtest

# Deep fundamental memo for one ticker (DESIGN.md §15): make research code=7203
research:
	uv run tse-scanner research $(code)

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__
