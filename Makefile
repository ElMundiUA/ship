# Ship cloud platform — operator Makefile.
#
# One-liner reference for the common dev/ops loops. Targets are thin shims
# over docker compose so a fresh checkout only needs `make bootstrap && make
# up && make smoke` to land on a working stack.
#
# Run `make help` to list targets.

SHELL := /bin/bash
COMPOSE ?= docker compose
PROJECT ?= ship
BACKEND_SVC := ship-server
WORKER_SVC := ship-worker
CONSOLE_SVC := console
DB_SVC := postgres

# Override on the command line, e.g.: make smoke SMOKE_BASE=http://staging:3001
SMOKE_BASE ?= http://localhost:3001
SMOKE_API ?= http://localhost:8100

.DEFAULT_GOAL := help

.PHONY: help bootstrap up down restart rebuild logs logs-server logs-worker \
        logs-console health smoke psql redis-cli migrate revision shell \
        test test-backend test-fast clean nuke status backup ps env-check

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
		/^[a-zA-Z0-9_.-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)

bootstrap: ## Generate .env with random secrets + Auth0 placeholders (idempotent).
	@./scripts/bootstrap.sh

env-check: ## Fail fast if .env is missing.
	@test -f .env || { echo "no .env — run: make bootstrap"; exit 1; }

up: env-check ## Build images + start the full stack in the background.
	$(COMPOSE) up -d --build

down: ## Stop the stack (keep volumes).
	$(COMPOSE) down

restart: down up ## Equivalent to make down && make up.

rebuild: env-check ## Force-rebuild images, then bring the stack up.
	$(COMPOSE) build --no-cache
	$(COMPOSE) up -d

ps: ## Show service status.
	$(COMPOSE) ps

status: ps ## Alias for ps.

logs: ## Tail logs for all services.
	$(COMPOSE) logs -f --tail=100

logs-server: ## Tail backend (ship-server) logs.
	$(COMPOSE) logs -f --tail=200 $(BACKEND_SVC)

logs-worker: ## Tail worker (arq) logs.
	$(COMPOSE) logs -f --tail=200 $(WORKER_SVC)

logs-console: ## Tail console (Next.js) logs.
	$(COMPOSE) logs -f --tail=200 $(CONSOLE_SVC)

health: ## Curl /healthz and /v1/health on the backend.
	@printf "/healthz   -> "
	@curl -fsS $(SMOKE_API)/healthz && echo
	@printf "/v1/health -> "
	@curl -fsS $(SMOKE_API)/v1/health && echo
	@printf "console    -> "
	@curl -fsS -o /dev/null -w "HTTP %{http_code}\n" $(SMOKE_BASE)/login

smoke: env-check ## Run the Playwright onboarding smoke test against a running stack.
	@cd scripts && npm install --silent --no-audit --no-fund
	SMOKE_BASE=$(SMOKE_BASE) SMOKE_API=$(SMOKE_API) \
	    node scripts/console-repo-onboarding-smoke.mjs

migrate: env-check ## Run alembic upgrade head against the live container.
	$(COMPOSE) exec $(BACKEND_SVC) alembic -c backend/alembic.ini upgrade head

revision: env-check ## Generate a new alembic revision. Use: make revision M="add foo".
	@test -n "$(M)" || { echo "Usage: make revision M=\"description\""; exit 2; }
	$(COMPOSE) exec $(BACKEND_SVC) alembic -c backend/alembic.ini revision --autogenerate -m "$(M)"

psql: ## Open a psql shell on the running Postgres.
	$(COMPOSE) exec $(DB_SVC) psql -U $${POSTGRES_USER:-ship} -d $${POSTGRES_DB:-ship}

redis-cli: ## Open redis-cli on the running Redis.
	$(COMPOSE) exec redis redis-cli

shell: ## Drop into a bash shell on the backend container.
	$(COMPOSE) exec $(BACKEND_SVC) bash

test: ## Run the backend pytest suite inside the container.
	$(COMPOSE) exec -T $(BACKEND_SVC) pytest backend/tests -x

test-backend: test ## Alias for test.

test-fast: ## Run only fast unit tests (-m "not slow").
	$(COMPOSE) exec -T $(BACKEND_SVC) pytest backend/tests -x -m "not slow"

backup: ## Snapshot Postgres into ./backups/ship-YYYYMMDD-HHMMSS.sql.gz.
	@mkdir -p backups
	@stamp=$$(date +%Y%m%d-%H%M%S); \
	  out="backups/ship-$$stamp.sql.gz"; \
	  $(COMPOSE) exec -T $(DB_SVC) pg_dump -U $${POSTGRES_USER:-ship} $${POSTGRES_DB:-ship} | gzip > $$out; \
	  echo "wrote $$out ($$(du -h $$out | cut -f1))"

clean: ## Stop the stack and drop the docker network. Keeps volumes (DB safe).
	$(COMPOSE) down --remove-orphans

nuke: ## ⚠️  Drop containers AND volumes — wipes Postgres + MinIO data.
	@read -p "Type 'wipe' to delete all data: " ans; \
	  [ "$$ans" = "wipe" ] && $(COMPOSE) down -v --remove-orphans \
	    || echo "aborted."
