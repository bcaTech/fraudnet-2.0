.PHONY: help bootstrap dev seed test test-unit test-integration test-contracts \
        lint typecheck format clean infra-up infra-down infra-logs load-gen \
        kafka-topics-create kafka-topics-list

SHELL := /bin/bash

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------

SERVICE ?=
COMPOSE := docker compose -f docker-compose.dev.yml

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-22s\033[0m %s\n", $$1, $$2}'

# ----------------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------------

bootstrap: ## Install Python venv, Node tooling, pre-commit hooks
	@command -v uv >/dev/null 2>&1 || (echo "Install uv: https://docs.astral.sh/uv/" && exit 1)
	uv sync --all-packages
	uv run pre-commit install
	@echo ""
	@echo "Bootstrap complete. Run 'make infra-up' to start the data plane."

# ----------------------------------------------------------------------------
# Local data plane
# ----------------------------------------------------------------------------

infra-up: ## Bring up Kafka, Postgres, Memgraph, Aerospike, MinIO, etc.
	$(COMPOSE) up -d
	@echo "Waiting for healthchecks..." && sleep 5
	$(COMPOSE) ps

infra-down: ## Tear down the local data plane (keeps volumes)
	$(COMPOSE) down

infra-nuke: ## Tear down AND wipe volumes (destroys local data)
	$(COMPOSE) down -v

infra-logs: ## Tail logs for the data plane
	$(COMPOSE) logs -f --tail=100

kafka-topics-create: ## Apply declarative topic definitions
	scripts/kafka_topics_apply.sh

kafka-topics-list: ## List Kafka topics in the dev cluster
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server kafka:9092 --list

seed: ## Populate sample data (numbers, wallets, alerts)
	uv run python scripts/seed_dev_data.py

# ----------------------------------------------------------------------------
# Develop
# ----------------------------------------------------------------------------

dev: ## Run all services in dev mode (or SERVICE=foo for one)
ifeq ($(SERVICE),)
	uv run honcho start -f Procfile.dev
else
	uv run --package $(SERVICE) uvicorn $(SERVICE).main:app --reload --port $$(scripts/port_for.sh $(SERVICE))
endif

# ----------------------------------------------------------------------------
# Quality gates
# ----------------------------------------------------------------------------

lint: ## Run ruff lint across the workspace
	uv run ruff check .
	uv run ruff format --check .

format: ## Auto-format with ruff
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run mypy across the workspace
	uv run mypy packages services

test: test-unit ## Default test target

test-unit: ## Fast unit tests, no Docker
	uv run pytest -m "not integration and not load" --maxfail=1

test-integration: ## Integration tests against ephemeral Docker services
	uv run pytest -m integration

test-contracts: ## Cross-service Avro / OpenAPI contract checks
	uv run python scripts/check_contracts.py

# ----------------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------------

load-gen: ## Run the synthetic event generator
	uv run python -m tools.load_gen.cli $(ARGS)

clean: ## Remove caches and build artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
	rm -rf .turbo coverage htmlcov
