SHELL := /bin/bash

# Load .env so POSTGRES_* are available to every recipe and exported to
# child processes (dbt reads them via env_var()).
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

COMPOSE := docker compose

.DEFAULT_GOAL := help

.PHONY: help setup db-up db-down db-reset db-logs db-psql db-check test test-integration dbt-debug lint format typecheck check hooks-install hooks-run

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create .env if missing and install dependencies
	@test -f .env || cp .env.example .env
	uv sync

db-up: ## Start PostgreSQL in the background
	$(COMPOSE) up -d

db-down: ## Stop PostgreSQL, keeping data
	$(COMPOSE) down

db-reset: ## Destroy the volume and recreate the database
	$(COMPOSE) down -v
	$(COMPOSE) up -d

db-logs: ## Tail PostgreSQL logs
	$(COMPOSE) logs -f postgres

db-psql: ## Open a psql shell against the warehouse
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

db-check: ## Verify the Python to PostgreSQL connection
	uv run tt-db-check

test: ## Run the test suite
	uv run pytest

test-integration: ## Run only integration tests
	uv run pytest -m integration

dbt-debug: ## Verify dbt can reach the warehouse
	uv run dbt debug --project-dir dbt --profiles-dir dbt

lint: ## Lint with ruff
	uv run ruff check .

format: ## Format with ruff
	uv run ruff format .

typecheck: ## Type check with mypy
	uv run mypy

check: ## Run lint and type checks (same tools as pre-commit)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

hooks-install: ## Install git hooks, then run them once (requires git init)
	uv run pre-commit install --install-hooks
	uv run pre-commit run --all-files

hooks-run: ## Run all pre-commit hooks against every file
	uv run pre-commit run --all-files
