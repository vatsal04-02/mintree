# Performance Architecture

Target (precise): **10,000+ aggregate HTTP API requests per second across horizontally scaled API replicas, measured on dedicated staging hardware.** Not 10,000 DB transactions/s, not 10,000 concurrent users, not 10,000 financial postings/s. Status: **not demonstrated** (see `PERFORMANCE_BENCHMARK_RESULTS.md`): this repository was measured on one shared 4-vCPU machine, where the honest result is a per-core cost, not a fleet capacity.

## 1. Shape of the system (unchanged, deliberately)
Modular monolith: FastAPI (sync handlers in a bounded thread pool) → PostgreSQL 16 (source of truth) with Redis (coordination + caches) and RabbitMQ/Celery (outbox consumers). Stateless API processes; any number of replicas behind a load balancer. No microservices, no Kafka, no Kubernetes were introduced: the measurements below show the first-order costs are per-request CPU and round trips, not architecture.

## 2. Request lifecycle and what each stage costs
`RateLimit → RequestContext (request id) → Metrics → AdmissionControl (≤ pool slots in flight; excess waits without holding a thread or connection; 503 after 5 s) → router → dependencies (session, permissions) → handler → response`.
Per-request budget before this milestone (measured, `py-spy`, one worker): 1 session+user query, 1 Redis round trip for the permission epoch, 1–3 business queries, ORM hydration, pydantic response validation. ≈ 10–11 ms CPU per request ⇒ ≈ 90–100 req/s per busy worker.

## 3. What was implemented in this milestone (each measured; see the results file)
| Change | Mechanism | Safety contract |
|---|---|---|
| **Per-process session cache** (`core/deps.py`) | caches the session row + user columns (never the password hash) + roles/permissions per token hash for ≤ 30 s | validated on every request against the Redis *authorization epoch*; logout, deactivation, password reset, role/permission changes and session revocation all bump the epoch after commit (`core/authz_cache.py`), so every replica drops its entries; expiry re-checked on every hit; Redis down ⇒ cache bypassed, database authoritative. Tests: `test_authz_cache.py` (logout, deactivation, reset, role change, expiry, forged cookie) |
| **Redis epoch/version memo** (`authz_cache.memo_get`) | a process re-reads a Redis key at most every 20 ms | other replicas see an invalidation within ≤ 20 ms (the committing process sees it at once). It removed ~29 % of busy CPU in the profile |
| **Versioned response cache for reference lists** (`core/rcache.py`, `core/crud.py`) | cached JSON bytes for master-data list endpoints, key = path + query + caller's permission signature | permission check always runs first; ORM commits touching the table bump its Redis version after commit; 60 s TTL as a safety net for raw-SQL writes; single-flight per key (no stampede); LRU bounded to 2,000 entries / 64 MB; Redis down ⇒ bypass. **Never used for stock allocation, QC release or postings.** Tests: `test_response_cache.py` |
| **Dashboard cache** (`analytics/router.py`) | 15 s cache of `management-summary` and `overview-period`, single-flight | the response carries `meta.cached`, `meta.generated_at`, `meta.max_age_s`, and the Overview shows "computed N s ago (refreshed at most every 15 s)" |
| Metrics | `mintree_session_cache_total`, `mintree_response_cache_total{ns,result}` | hit/miss/bypass visible in Prometheus |

## 4. Traffic classes (each benchmarked separately; cache hits are never blended with database writes)
A cacheable reference reads · B uncached DB reads · C dashboards · D normal business writes · E hot-stock reservations (contended) · F production/quality-style postings (proxy: stock issues) · G financial postings (manual journals) · H background jobs. `tests/load/scenarios/classes.js` runs one class at one constant arrival rate (open loop); `tests/load/run_classes.sh` starts an API configuration on a copy of the demo database and sweeps rates.

## 5. Connection and worker budget
Per process: pool 10 (+0 overflow), admission slots = pool, thread pool = slots + 4. Total DB connections = processes × 10 (+ workers). On this 4-vCPU box 3 API processes were used because k6 needs a core; the worker sweep is in the results file. Rule: **processes ≈ physical cores reserved for the API; never more connections than `max_connections` minus headroom**; PgBouncer (transaction pooling, `MINTREE_DB_PREPARE_THRESHOLD=none`) is the next step beyond ~200 connections and is documented but was not benchmarked here.

## 6. Not done / not claimed
* No read replica, no PgBouncer run, no materialised views: the measured bottleneck for A/C was per-request CPU and Redis/DB round trips, which the changes above address; for B it is ORM + pydantic cost per query (≈ 8 ms CPU/request), unchanged.
* Class H (background jobs) needs RabbitMQ; none is installed in this sandbox, so queue throughput was **not** re-measured (the 10,000-job recovery run from the earlier milestone stands as historical evidence).
* No 30-minute sustained target, no 2-hour soak, no multi-node run, no database-failover test.
