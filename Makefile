# Mintree Factory OS: demo and operations entry points. See docs/DEMO_RUNBOOK.md.
# Needs: Docker (Postgres/Redis/RabbitMQ), Python 3.12 venv at backend/.venv, Node 20+ for the web app.
SHELL := bash
ifeq ($(OS),Windows_NT)
PY ?= backend/.venv/Scripts/python.exe
else
PY ?= backend/.venv/bin/python
endif
DEMO_DB      ?= mintree_demo
PG_URL       ?= postgresql+psycopg://mintree:mintree@localhost:5433
API_PORT     ?= 8101
WEB_PORT     ?= 3101
DEMO_API     ?= http://127.0.0.1:$(API_PORT)
export MINTREE_ENVIRONMENT := demo
export MINTREE_DATABASE_URL := $(PG_URL)/$(DEMO_DB)
export MINTREE_DEMO_LIVE ?= true
export MINTREE_BROKER_URL ?= amqp://guest:guest@127.0.0.1:5672/demo      # its own RabbitMQ vhost: demo traffic never mixes with test or other queues
export DEMO_API
DC := docker compose -f docker-compose.dev.yml

.PHONY: up down db migrate seed seed-scale warm demo-check demo-snapshot demo-reset demo-up demo-down demo-export demo-import perf load soak test test-demo ui-demo

up:                         ## start Postgres, Redis, RabbitMQ and create the demo/test databases
	$(DC) up -d
	@until docker exec mintree-dev-db-1 pg_isready -U mintree >/dev/null 2>&1; do sleep 1; done
	@docker exec mintree-dev-rabbitmq-1 rabbitmqctl add_vhost demo >/dev/null 2>&1; docker exec mintree-dev-rabbitmq-1 rabbitmqctl set_permissions -p demo guest ".*" ".*" ".*" >/dev/null 2>&1; true
	@for d in $(DEMO_DB) mintree_test; do docker exec mintree-dev-db-1 psql -U mintree -tc "select 1 from pg_database where datname='$$d'" | grep -q 1 || docker exec mintree-dev-db-1 psql -U mintree -c "create database $$d"; done
down:
	$(DC) down

migrate:                    ## bring the demo database to the current schema
	cd backend && ../$(PY) -m alembic upgrade head

seed:                       ## story-sized demo (about 2 minutes): schema + base data + every scenario, through the real API. Wipes the demo database.
	cd backend && ../$(PY) -m scripts.demo_data reset --confirm-reset-demo-database $(DEMO_DB) && ../$(PY) -m scripts.demo_data init

seed-scale:                 ## full-scale history (hours; resumable): 2,500 customers, 25k orders, 4k batches ... all through the real API, 4 parallel workers
	cd backend && ../$(PY) -m scripts.demo.bulk masters && ../$(PY) -m scripts.demo.bulk stock && ../$(PY) -m scripts.demo.bulk run --workers 4 --orders 8000 --market 17000 --purchases 2000 --production 4000

warm:                       ## prime caches, pools and first-paint dashboards on the running API
	cd backend && ../$(PY) -m scripts.warm --base $(DEMO_API) --email demo.admin@example.com --password 'Mintree-Demo#2026' --passes 2

demo-check:                 ## one-command pre-flight: red/green checklist (about a minute)
	cd backend && ../$(PY) -m scripts.demo_check --api $(DEMO_API)

demo-snapshot:              ## take the golden snapshot (after a green demo-check, before the demo)
	cd backend && ../$(PY) -m scripts.demo_ops snapshot

demo-reset:                 ## restore the golden snapshot (seconds); keeps your session; re-anchors dates; clears caches
	cd backend && ../$(PY) -m scripts.demo_ops reset

demo-up:                    ## API (2 workers), outbox publisher, Live Mode worker, web app: all in the background, logs in ./logs
	@mkdir -p logs
	cd backend && (nohup ../$(PY) -m uvicorn app.main:app --port $(API_PORT) --workers 2 --log-level warning > ../logs/api.log 2>&1 &)
	cd backend && (nohup ../$(PY) -m app.workers.outbox_publisher > ../logs/publisher.log 2>&1 &)
	cd backend && (nohup ../$(PY) -m celery -A app.workers.celery_app worker -Q default,notifications,integrations,imports,reports,documents -c 2 -l warning > ../logs/worker.log 2>&1 &)
	cd backend && (nohup ../$(PY) -m app.demo_live > ../logs/live.log 2>&1 &)
	cd frontend && (BACKEND_URL=http://127.0.0.1:$(API_PORT) nohup npx next start -p $(WEB_PORT) > ../logs/web.log 2>&1 &)
	@echo "API :$(API_PORT)  web :$(WEB_PORT)  (build the web app once with: cd frontend && BACKEND_URL=http://127.0.0.1:$(API_PORT) npm run build)"

demo-down:                  ## stop everything demo-up started
	-@fuser -k $(API_PORT)/tcp $(WEB_PORT)/tcp 2>/dev/null; pkill -f "app.demo_live" ; pkill -f "outbox_publisher" ; pkill -f "celery -A app.workers" ; true

demo-export:                ## write the golden snapshot to ops/demo/golden.dump (for the fallback instance)
	@mkdir -p ops/demo
	docker exec mintree-dev-db-1 pg_dump -U mintree -Fc $(DEMO_DB)_golden > ops/demo/golden.dump
demo-import:                ## load ops/demo/golden.dump into a fresh local database and snapshot it
	docker exec mintree-dev-db-1 psql -U mintree -c "drop database if exists $(DEMO_DB)_golden with (force)" -c "create database $(DEMO_DB)_golden"
	docker exec -i mintree-dev-db-1 pg_restore -U mintree -d $(DEMO_DB)_golden --no-owner < ops/demo/golden.dump
	$(MAKE) demo-reset

perf:                       ## read-latency probe against the running API: p50/p95/p99 per class vs the budgets
	cd backend && ../$(PY) -m scripts.warm --base $(DEMO_API) --email demo.admin@example.com --password 'Mintree-Demo#2026' --passes 15 --report --json ../tests/load/results/perf-probe.json
load:                       ## 50 virtual users, 80% reads / 20% writes, 15 minutes (override: LOAD_ARGS="--users 50 --minutes 15")
	$(PY) tests/demo/load.py --base $(DEMO_API) $(LOAD_ARGS)
soak:                       ## 10 users for 60 minutes, sampling API memory/CPU
	$(PY) tests/demo/load.py --base $(DEMO_API) --users 10 --minutes 60 --label soak

test:                       ## backend suite (own test database)
	cd backend && ../$(PY) -m pytest -q -p no:cacheprovider
test-demo:                  ## the demo run-of-show against the running demo API
	$(PY) -m pytest tests/demo -q -p no:cacheprovider
ui-demo:                    ## browser run-of-show (needs the web app on WEB_PORT and Playwright)
	cd frontend && BASE_URL=http://localhost:$(WEB_PORT) DEMO_PASSWORD='Mintree-Demo#2026' node ../tests/demo/ui.mjs
