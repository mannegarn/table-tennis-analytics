SHELL := /bin/bash

# Load .env so POSTGRES_* are available to every recipe and exported to
# child processes (dbt reads them via env_var()).
ifneq (,$(wildcard ./.env))
    include .env
    export
endif

COMPOSE := docker compose

# Optional filters, forwarded to the collectors as flags only when set.
YEARS ?=
LIMIT ?=
EVENT_IDS ?=
PLAYER_IDS ?=
SELECT ?=
TEST_DATABASE_NAME ?= tt_stats_test

ARGS_years := $(if $(YEARS),--years $(YEARS))
ARGS_limit := $(if $(LIMIT),--limit $(LIMIT))
ARGS_events := $(if $(EVENT_IDS),--event-ids $(EVENT_IDS))
ARGS_players := $(if $(PLAYER_IDS),--player-ids $(PLAYER_IDS))
ARGS_select := $(if $(SELECT),--select $(SELECT))

# Every collector, in dependency order: calendars first, then sponsors and
# match entries, then match details & commentary in parallel (both read
# match_entries_unpacked), and players last (reads match_details_players_unpacked).
COLLECT_PRE := events-collect event-sponsors-collect matches-collect
COLLECT_PARALLEL := match-details-collect match-commentary-collect
COLLECT_POST := player-details-collect ittf-players-seed

# Abort a recipe whose required variable is empty, instead of running it with a
# blank argument. The value comes from .env or the command line.
define require
@test -n "$($(1))" || { printf '%s is empty; set it in .env or pass it on the command line\n' '$(1)' >&2; exit 1; }
endef

.DEFAULT_GOAL := help

# Collectors share one pending queue and one API; never run them side by side.
.NOTPARALLEL:

.PHONY: help setup db-up db-down db-reset db-logs db-psql db-test-psql db-check db-test-check db-migrate events-collect events-unpack events-unpack-preview matches-collect matches-unpack matches-unpack-preview match-details-collect match-commentary-collect event-sponsors-collect player-details-collect collect-all ittf-players-seed update-summary test test-integration dbt-debug dbt-run dbt-test dbt-clean lint lint-sql format typecheck check hooks-install hooks-run

help: ## Show available commands
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-30s\033[0m %s\n", $$1, $$2}'

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
	$(call require,POSTGRES_DB)
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

db-test-psql: ## Open a psql shell against the integration-test database
	$(call require,POSTGRES_DB)
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) -d $(TEST_DATABASE_NAME)

db-check: ## Verify the Python to PostgreSQL connection
	uv run tt-db-check

db-test-check: ## Verify the test database exists, is migrated and how many fixture rows it holds
	@$(COMPOSE) exec -T postgres psql -U $(POSTGRES_USER) -d postgres -tAc \
		"SELECT 1 FROM pg_database WHERE datname = '$(TEST_DATABASE_NAME)'" \
		| grep -qx 1 \
		|| { echo "missing database $(TEST_DATABASE_NAME); see db/init/000_create_test_db.sql"; exit 1; }
	@$(COMPOSE) exec -T postgres psql -U $(POSTGRES_USER) -d $(TEST_DATABASE_NAME) -tAc \
		"SELECT format('migrations=%s events_raw=%s events_unpacked=%s match_raw=%s match_unpacked=%s runs=%s', \
			(SELECT count(*) FROM ingest.schema_version), \
			(SELECT count(*) FROM ingest.events_by_year_raw), \
			(SELECT count(*) FROM ingest.events_unpacked), \
			(SELECT count(*) FROM ingest.match_entries_payloads_raw), \
			(SELECT count(*) FROM ingest.match_entries_unpacked), \
			(SELECT count(*) FROM ingest.ingest_runs));"

db-migrate: ## Apply db/migrations to the warehouse
	uv run tt-db-migrate

events-collect: ## Collect the pending calendar years into ingest.events_by_year_raw
	uv run tt-events-collect $(strip $(ARGS_years) $(ARGS_limit))

events-unpack: ## Unpack landed calendars into ingest.events_unpacked
	uv run tt-events-unpack $(ARGS_years)

events-unpack-preview: ## Run the unpack for one year, then roll it back
	uv run tt-events-unpack --years $(or $(YEARS),2023) --dry-run

matches-collect: ## Collect match lists for pending events into ingest.match_entries_payloads_raw
	uv run tt-matches-collect $(strip $(ARGS_events) $(ARGS_limit))

matches-unpack: ## Unpack landed match lists into ingest.match_entries_unpacked
	uv run tt-matches-unpack $(ARGS_events)

matches-unpack-preview: ## Run the match unpack with EVENT_IDS, then roll it back
	$(call require,EVENT_IDS)
	uv run tt-matches-unpack --event-ids $(EVENT_IDS) --dry-run

match-details-collect: ## Collect matchcentre details for pending matches into ingest.match_details_raw
	uv run tt-match-details-collect $(strip $(ARGS_events) $(ARGS_limit))

match-commentary-collect: ## Collect match commentary for pending matches into ingest.match_commentary_raw
	uv run tt-match-commentary-collect $(strip $(ARGS_events) $(ARGS_limit))

event-sponsors-collect: ## Collect sponsor details for pending events into ingest.event_sponsors_raw
	uv run tt-event-sponsors-collect $(strip $(ARGS_events) $(ARGS_limit))

player-details-collect: ## Collect player details for pending players into ingest.player_details_raw
	uv run tt-player-details-collect $(strip $(ARGS_players) $(ARGS_limit))

collect-all: ## Run every collector (details + commentary in parallel); report failures at the end
	@failed=""; \
	for step in $(COLLECT_PRE); do \
		printf '\n==> %s\n' "$$step"; \
		if $(MAKE) --no-print-directory "$$step"; then \
			printf '    ok\n'; \
		else \
			printf '    failed: %s\n' "$$step" >&2; \
			failed="$$failed $$step"; \
		fi; \
	done; \
	printf '\n==> parallel: %s\n' "$(COLLECT_PARALLEL)"; \
	pids=""; \
	names=""; \
	for step in $(COLLECT_PARALLEL); do \
		$(MAKE) --no-print-directory "$$step" & \
		pids="$$pids $$!"; \
		names="$$names $$step"; \
	done; \
	set -- $$names; \
	for pid in $$pids; do \
		step="$$1"; shift; \
		if wait "$$pid"; then \
			printf '    ok: %s\n' "$$step"; \
		else \
			printf '    failed: %s\n' "$$step" >&2; \
			failed="$$failed $$step"; \
		fi; \
	done; \
	for step in $(COLLECT_POST); do \
		printf '\n==> %s\n' "$$step"; \
		if $(MAKE) --no-print-directory "$$step"; then \
			printf '    ok\n'; \
		else \
			printf '    failed: %s\n' "$$step" >&2; \
			failed="$$failed $$step"; \
		fi; \
	done; \
	if [ -n "$$failed" ]; then \
		printf '\nSome collectors reported failures:%s (see logs/tt_stats.log)\n' "$$failed" >&2; \
		exit 1; \
	fi; \
	printf '\nAll collectors finished.\n'

ittf-players-seed: ## Seed ingest.ittf_player_profiles from data/20260615_master_players.csv
	uv run tt-ittf-players-seed

update-summary: ## Generate docs/data-summary.md from the PostgreSQL database
	uv run tt-update-summary

test: ## Run the test suite
	uv run pytest

test-integration: ## Run only integration tests
	uv run pytest -m integration

dbt-debug: ## Verify dbt can reach the warehouse
	uv run dbt debug --project-dir dbt --profiles-dir dbt

dbt-run: ## Build dbt models in the analytics schema (optional: SELECT=model)
	uv run dbt run --project-dir dbt --profiles-dir dbt $(ARGS_select)

dbt-test: ## Run dbt data tests against the analytics schema (optional: SELECT=model)
	uv run dbt test --project-dir dbt --profiles-dir dbt $(ARGS_select)

dbt-clean: ## Remove dbt compiled artifacts and logs (dbt/target, dbt/logs)
	uv run dbt clean --project-dir dbt --profiles-dir dbt

lint: ## Lint with ruff
	uv run ruff check .

format: ## Format with ruff
	uv run ruff format .

typecheck: ## Type check with mypy
	uv run mypy

lint-sql: ## Lint SQL with sqlfluff (dbt + db as jinja, src as psycopg placeholders)
	uv run sqlfluff lint dbt/models dbt/tests dbt/macros db
	uv run sqlfluff lint src --templater placeholder

check: ## Run lint and type checks (same tools as pre-commit)
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy
	$(MAKE) --no-print-directory lint-sql

hooks-install: ## Install git hooks, then run them once (requires git init)
	uv run pre-commit install --install-hooks
	uv run pre-commit run --all-files

hooks-run: ## Run all pre-commit hooks against every file
	uv run pre-commit run --all-files
