SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
unexport VIRTUAL_ENV

.PHONY: help install plugin-install plugin-uninstall lint format check test test-unit validate clean start start-interactive report report-session

## help: Show available targets
help:
	@echo "DevBench — Make Targets"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
	@echo ""

## install: Install runtime and dev dependencies
install:
	uv sync --all-extras

## plugin-install: Register devbench marketplace and install plugin (user scope)
plugin-install:
	claude plugin marketplace add ./plugin --scope user
	claude plugin install devbench --scope user

## plugin-uninstall: Uninstall devbench plugin and remove marketplace
plugin-uninstall:
	claude plugin uninstall devbench
	claude plugin marketplace remove devbench

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
test: test-unit

## test-unit: Run unit tests
test-unit:
	uv run pytest tests/ -v --tb=short -q

## validate: Full validation (check + test)
validate: check test
	@echo "All validations passed"

## clean: Remove build artifacts
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true

## start: Run orchestrate skill non-interactively via Claude Agent SDK
start:
	uv run python -m devbench.cli start

## start-interactive: Launch interactive Claude session with devbench plugin loaded
start-interactive:
	claude --plugin-dir plugin/devbench

## report: Show backlog progress report (full session)
report:
	uv run python -m devbench.cli report

## report-session: Show progress since a timestamp (e.g. make report-session SINCE=2026-03-05T16:13:00Z)
report-session:
	uv run python -m devbench.cli report "$(SINCE)"

