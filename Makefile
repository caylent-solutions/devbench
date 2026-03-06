SHELL := /bin/bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

.PHONY: help install lint format check test test-unit test-functional validate clean run-backlog start start-interactive report

## help: Show available targets
help:
	@echo "DevBench — Make Targets"
	@echo ""
	@grep -E '^## ' $(MAKEFILE_LIST) | sed 's/^## /  /'
	@echo ""

## install: Install runtime and dev dependencies
install:
	python3 -m pip install -r requirements-dev.txt

## lint: Run ruff linter and bandit security scan
lint:
	python3 -m ruff check . --config ruff.toml
	python3 -m bandit -r . -ll --exclude ./tests

## format: Auto-format code with ruff
format:
	python3 -m ruff format . --config ruff.toml
	python3 -m ruff check . --fix --config ruff.toml

## check: Run lint + type check
check: lint
	cd .. && python3 -m mypy judges/ --ignore-missing-imports --explicit-package-bases --exclude judges/__main__.py

## test: Run all tests
test: test-unit test-functional

## test-unit: Run unit tests
test-unit:
	python3 -m pytest tests/ -v --tb=short -q

## test-functional: Run functional tests
test-functional:
	python3 -m pytest tests/test_functional_judges.py -v --tb=short -q

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
	cd .. && python3 -m judges.cli report

## report-session: Show progress since a timestamp (e.g. make report-session SINCE=2026-03-05T16:13:00Z)
report-session:
	cd .. && python3 -m judges.cli report "$(SINCE)"

## run-backlog: Execute the autonomous backlog orchestrator (foreground, assumes GH_TOKEN is set)
run-backlog:
	cd .. && python3 -m judges.orchestrator
