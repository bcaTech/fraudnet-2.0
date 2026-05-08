.PHONY: help bootstrap dev seed seed-demo test test-unit test-integration test-contracts \
        test-e2e lint typecheck format clean \
        infra-up infra-down infra-nuke infra-logs logs \
        services-up services-down services-restart \
        kafka-topics-create kafka-topics-list \
        migrations install-packages

SHELL := /bin/bash

# ----------------------------------------------------------------------------
# Defaults
# ----------------------------------------------------------------------------

SERVICE ?=
COMPOSE := docker compose -f docker-compose.dev.yml

# Application services (13 — Phase 1). Infra services live in INFRA_SERVICES.
APP_SERVICES := ingest-momo ingest-voice ingest-sms \
                stream-features stream-graph \
                brain-behavioural brain-content brain-otp-guard \
                decisions action-tier1 action-tier2 \
                api-noc api-customer compliance

INFRA_SERVICES := postgres memgraph aerospike kafka schema-registry \
                  redis minio minio-bootstrap keycloak \
                  flink-jobmanager flink-taskmanager \
                  otel-collector prometheus grafana \
                  kafka-init db-migrate

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

install-packages: ## Install all workspace packages in editable mode (host venv)
	scripts/install_packages.sh

# ----------------------------------------------------------------------------
# Local data plane — infrastructure only
# ----------------------------------------------------------------------------

infra-up: ## Bring up infra (Kafka, Postgres, Memgraph, Aerospike, …) only
	$(COMPOSE) up -d $(INFRA_SERVICES)
	@echo "Waiting for healthchecks…" && sleep 5
	$(COMPOSE) ps

infra-down: ## Stop infra (keeps volumes)
	$(COMPOSE) stop $(INFRA_SERVICES)

infra-nuke: ## Tear down everything AND wipe volumes
	$(COMPOSE) down -v

infra-logs: ## Tail logs for the data plane
	$(COMPOSE) logs -f --tail=100 $(INFRA_SERVICES)

# ----------------------------------------------------------------------------
# Application services
# ----------------------------------------------------------------------------

services-up: ## Bring up all 13 application services (depends on infra)
	$(COMPOSE) up -d $(APP_SERVICES)
	$(COMPOSE) ps $(APP_SERVICES)

services-down: ## Stop all application services
	$(COMPOSE) stop $(APP_SERVICES)

services-restart: ## Restart all application services
	$(COMPOSE) restart $(APP_SERVICES)

logs: ## Tail logs (LOGS=svc1,svc2 to filter — default: all app services)
ifeq ($(LOGS),)
	$(COMPOSE) logs -f --tail=100 $(APP_SERVICES)
else
	$(COMPOSE) logs -f --tail=100 $(subst $(comma), ,$(LOGS))
endif

kafka-topics-create: ## Apply declarative topic definitions (idempotent)
	$(COMPOSE) run --rm kafka-init

kafka-topics-list: ## List Kafka topics in the dev cluster
	$(COMPOSE) exec kafka kafka-topics --bootstrap-server kafka:9092 --list

migrations: ## Apply Postgres migrations (api-noc + compliance)
	$(COMPOSE) run --rm db-migrate

seed: ## Populate the small dev fixture set (numbers, wallets, alerts)
	uv run python scripts/seed_dev_data.py

seed-demo: ## Populate full demo dataset (Memgraph + Aerospike + Postgres)
	uv run python scripts/seed_demo.py

# ----------------------------------------------------------------------------
# Develop — single service with hot-reload
# ----------------------------------------------------------------------------

dev: ## Run a single service in dev mode (SERVICE=ingest-voice make dev)
ifeq ($(SERVICE),)
	@echo "Run all services in containers: 'make services-up'"
	@echo "Or run a single service on the host: 'SERVICE=<name> make dev'"
	@echo "  available: $(APP_SERVICES)"
	@exit 1
else
	$(COMPOSE) up -d --build $(SERVICE)
	$(COMPOSE) logs -f --tail=100 $(SERVICE)
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
	uv run pytest -m "not integration and not load and not e2e" --maxfail=1

test-integration: ## Integration tests against ephemeral Docker services
	uv run pytest -m integration

test-contracts: ## Cross-service Avro / OpenAPI contract checks
	uv run python scripts/check_contracts.py

test-e2e: ## End-to-end pipeline test against the live stack
	uv run pytest tests/e2e -m e2e -s

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

# Helpful for the LOGS=a,b filter trick.
comma := ,
