UV_CACHE_DIR ?= $(CURDIR)/.cache/uv
UV := UV_CACHE_DIR=$(UV_CACHE_DIR) uv

.PHONY: sync db-up db-down migrate migrate-check api worker test test-db lint typecheck audit web-install web-build container-build check

sync:
	$(UV) sync --extra dev --frozen

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	$(UV) run alembic upgrade head

migrate-check:
	$(UV) run alembic upgrade head
	$(UV) run alembic downgrade base
	$(UV) run alembic upgrade head

api:
	$(UV) run budget-api

worker:
	$(UV) run budget-worker

test:
	$(UV) run pytest

test-db:
	BUDGET_RUN_DB_TESTS=1 $(UV) run pytest tests/integration

lint:
	$(UV) run ruff check apps packages migrations tests
	$(UV) run ruff format --check apps packages migrations tests

typecheck:
	$(UV) run mypy apps packages tests

audit:
	$(UV) run pip-audit
	cd apps/web && npm audit --audit-level=high

web-install:
	cd apps/web && npm ci

web-build:
	cd apps/web && npm run build

container-build:
	bash scripts/build-containers.sh

check: lint typecheck test web-build
