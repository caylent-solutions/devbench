SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
unexport VIRTUAL_ENV

.PHONY: help install lint format check test test-unit test-functional validate clean run-backlog start start-interactive report

## help: Show available targets
help:
	@echo "DevBench — Make Targets"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
	@echo ""

## install: Install runtime and dev dependencies
install:
	uv sync --all-extras

## lint: Run ruff linter and bandit security scan
lint:
	uv run ruff check .
	uv run bandit -r . -ll --exclude ./tests,./.venv

## format: Auto-format code with ruff
format:
	uv run ruff format .
	uv run ruff check . --fix

## check: Run lint + type check
check:
	uv run ruff check .
	uv run mypy .

## test: Run all tests
test: test-unit test-functional

## test-unit: Run unit tests
test-unit:
	uv run pytest tests/ -v --tb=short -q --ignore=tests/functional

## test-functional: Run functional tests
test-functional:
	uv run pytest tests/functional/ -v --tb=short -q

## validate: Full validation (check + test)
validate: check test
	@echo "All validations passed"

## clean: Remove build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

## start: Auth GitHub, refresh token, launch backlog orchestrator in background
start:
	@bash scripts/start.sh

## start-interactive: Auth GitHub, launch interactive Claude session (pause/steer/resume)
start-interactive:
	@bash scripts/start-interactive.sh

## report: Show backlog progress report (full session)
report:
	uv run python -m devbench.cli report

## report-session: Show progress since a timestamp (e.g. make report-session SINCE=2026-03-05T16:13:00Z)
report-session:
	uv run python -m devbench.cli report "$(SINCE)"

## run-backlog: Execute the autonomous backlog orchestrator (foreground, assumes GH_TOKEN is set)
run-backlog:
	uv run python -m devbench.execution.orchestrator
