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
DEV_BACKEND_HOST ?= 127.0.0.1
DEV_BACKEND_PORT ?= 8100
DEV_CONSOLE_PORT ?= 3001
DEV_API_URL ?= http://localhost:$(DEV_BACKEND_PORT)

# Override on the command line, e.g.: make smoke SMOKE_BASE=http://staging:3001
SMOKE_BASE ?= http://localhost:3001
SMOKE_API ?= http://localhost:8100

.DEFAULT_GOAL := help

.PHONY: help bootstrap up down restart rebuild logs logs-server logs-worker \
        logs-console health smoke psql redis-cli migrate revision shell \
        test test-backend test-fast clean nuke status backup backup-prune ps env-check \
        dev-env-check dev-port-backend dev-port-console dev-migrate dev-backend \
        dev-console dev-local prod-up prod-down prod-logs bunny-deploy bunny-deploy-dry

help: ## Show this help.
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} \
		/^[a-zA-Z0-9_.-]+:.*##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' \
		$(MAKEFILE_LIST)

bootstrap: ## Generate .env with random secrets + Auth0 placeholders (idempotent).
	@./scripts/bootstrap.sh

env-check: ## Fail fast if .env is missing.
	@test -f .env || { echo "no .env — run: make bootstrap"; exit 1; }

dev-env-check: env-check ## Fail fast if .venv is missing for direct local dev.
	@test -x .venv/bin/python || { echo "no .venv — run: python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-backend.txt"; exit 1; }

dev-port-backend: ## Fail if the local backend dev port is already in use.
	@if lsof -iTCP:$(DEV_BACKEND_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "port $(DEV_BACKEND_PORT) is already in use; stop that process before running local backend dev"; \
		lsof -iTCP:$(DEV_BACKEND_PORT) -sTCP:LISTEN; \
		exit 1; \
	fi

dev-port-console: ## Fail if the local console dev port is already in use.
	@if lsof -iTCP:$(DEV_CONSOLE_PORT) -sTCP:LISTEN >/dev/null 2>&1; then \
		echo "port $(DEV_CONSOLE_PORT) is already in use; stop that process before running local console dev"; \
		lsof -iTCP:$(DEV_CONSOLE_PORT) -sTCP:LISTEN; \
		exit 1; \
	fi

dev-migrate: dev-env-check ## Run Alembic from .venv against DATABASE_URL/ALEMBIC_DATABASE_URL in .env.
	@node scripts/run-with-dotenv.mjs -- \
		.venv/bin/alembic -c backend/alembic.ini upgrade head

dev-backend: dev-env-check dev-port-backend ## Run FastAPI locally from .venv using root .env.
	@node scripts/run-with-dotenv.mjs \
		--set SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS=true \
		--default SHIP_PUBLIC_URL=http://localhost:$(DEV_BACKEND_PORT) \
		--default SHIP_CONSOLE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		-- .venv/bin/uvicorn backend.app.main:app --reload --host $(DEV_BACKEND_HOST) --port $(DEV_BACKEND_PORT)

dev-console: env-check dev-port-console ## Run the console locally against the local backend and root .env.
	@node scripts/run-with-dotenv.mjs \
		--default SHIP_API_URL=$(DEV_API_URL) \
		--default APP_BASE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		--default SHIP_CONSOLE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		-- npm run dev --prefix console

dev-local: dev-env-check dev-port-backend dev-port-console ## Run backend + console locally against shared dev infrastructure.
	@set -euo pipefail; \
		backend_pid=""; console_pid=""; \
		cleanup() { \
			[ -n "$$backend_pid" ] && kill "$$backend_pid" >/dev/null 2>&1 || true; \
			[ -n "$$console_pid" ] && kill "$$console_pid" >/dev/null 2>&1 || true; \
			wait >/dev/null 2>&1 || true; \
		}; \
		trap cleanup INT TERM EXIT; \
		( node scripts/run-with-dotenv.mjs \
		  --set SHIP_ALLOW_LOCAL_AUTH0_CALLBACKS=true \
		  --default SHIP_PUBLIC_URL=http://localhost:$(DEV_BACKEND_PORT) \
		  --default SHIP_CONSOLE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		  -- .venv/bin/uvicorn backend.app.main:app --reload --host $(DEV_BACKEND_HOST) --port $(DEV_BACKEND_PORT) ) & backend_pid=$$!; \
		( node scripts/run-with-dotenv.mjs \
		  --default SHIP_API_URL=$(DEV_API_URL) \
		  --default APP_BASE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		  --default SHIP_CONSOLE_URL=http://localhost:$(DEV_CONSOLE_PORT) \
		  -- npm run dev --prefix console ) & console_pid=$$!; \
		wait "$$backend_pid" "$$console_pid"

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

logs-worker: ## Tail worker (arq) logs (requires `--profile worker`).
	$(COMPOSE) --profile worker logs -f --tail=200 $(WORKER_SVC)

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

redis-cli: ## Open redis-cli on the running Redis (requires `--profile worker`).
	$(COMPOSE) --profile worker exec redis redis-cli

shell: ## Drop into a bash shell on the backend container.
	$(COMPOSE) exec $(BACKEND_SVC) bash

test: ## Run the backend pytest suite inside the container.
	$(COMPOSE) exec -T $(BACKEND_SVC) pytest backend/tests -x

test-backend: test ## Alias for test.

test-fast: ## Run only fast unit tests (-m "not slow").
	$(COMPOSE) exec -T $(BACKEND_SVC) pytest backend/tests -x -m "not slow"

backup: ## Snapshot Postgres into ./backups/ (rotates per BACKUP_KEEP, default 14).
	@./scripts/backup-postgres.sh

backup-prune: ## Drop old snapshots older than BACKUP_KEEP, no new dump.
	@BACKUP_PRUNE_ONLY=1 ./scripts/backup-postgres.sh

prod-up: env-check ## Bring the stack up with the Caddy/HTTPS overlay (single-VPS prod).
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml up -d --build

prod-down: ## Tear down the prod-overlay stack (keeps volumes).
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml down

prod-logs: ## Tail logs for all prod-overlay services (Caddy + app).
	$(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml logs -f --tail=200

bunny-deploy: ## Roll backend + console images to Bunny Magic Containers (uses scripts/bunny-deploy-platform.mjs).
	@test -n "$(IMAGE_TAG)" || { echo "Usage: make bunny-deploy IMAGE_TAG=main-abc1234"; exit 2; }
	IMAGE_TAG=$(IMAGE_TAG) node scripts/bunny-deploy-platform.mjs

bunny-deploy-dry: ## Same as bunny-deploy but no API writes (DRY_RUN=1).
	@test -n "$(IMAGE_TAG)" || { echo "Usage: make bunny-deploy-dry IMAGE_TAG=main-abc1234"; exit 2; }
	DRY_RUN=1 IMAGE_TAG=$(IMAGE_TAG) node scripts/bunny-deploy-platform.mjs

clean: ## Stop the stack and drop the docker network. Keeps volumes (DB safe).
	$(COMPOSE) down --remove-orphans

nuke: ## ⚠️  Drop containers AND volumes — wipes Postgres + MinIO data.
	@read -p "Type 'wipe' to delete all data: " ans; \
	  [ "$$ans" = "wipe" ] && $(COMPOSE) down -v --remove-orphans \
	    || echo "aborted."
