SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help
unexport VIRTUAL_ENV

.PHONY: help install plugin-install plugin-uninstall lint lint-ruff lint-bandit format format-check typecheck test test-unit test-coverage validate clean start start-interactive report report-session pre-commit-check pre-push-check

## help: Show available targets
help:
	@echo "DevBench -- Make Targets"
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

## lint-ruff: Run ruff linter
lint-ruff:
	uv run ruff check .

## lint-bandit: Run bandit security scan
lint-bandit:
	uv run bandit -r . -ll --exclude ./tests,./.venv

## lint: Run all linters (ruff + bandit)
lint: lint-ruff lint-bandit

## format: Auto-format code with ruff
format:
	uv run ruff format .
	uv run ruff check . --fix

## format-check: Check formatting without modifying files
format-check:
	uv run ruff format --check .

## typecheck: Run mypy type checking
typecheck:
	uv run mypy .

## test-unit: Run unit tests
test-unit:
	uv run pytest tests/ -v --tb=short -q

## test-coverage: Run tests with coverage report (fails below 90%)
test-coverage:
	uv run pytest tests/ --cov=devbench --cov-report=term-missing --cov-fail-under=90

## test: Run all tests
test: test-unit

## validate: Full validation (all checks -- identical to CI and pre-push)
validate: lint-ruff lint-bandit format-check typecheck test-coverage
	@echo "All validations passed"

## pre-commit-check: Checks that run on every commit (fast)
pre-commit-check: lint-ruff format-check
	@echo "Pre-commit checks passed"

## pre-push-check: Checks that run before push (full -- identical to CI)
pre-push-check: validate
	@echo "Pre-push checks passed"

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
