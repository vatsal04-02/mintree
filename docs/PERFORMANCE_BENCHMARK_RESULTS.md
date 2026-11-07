# Performance Benchmark Results

> ## Verdict
> **The 10,000 requests-per-second target is NOT demonstrated.** What was measured is the per-core cost and the throughput of one API node, per traffic class, on one shared 4-vCPU machine. Cacheable reads now sustain **≈ 500–790 req/s** on that machine (was ≈ 175–250), dashboards **≈ 420–620 req/s** (was ≈ 85–160), at the latencies below. Reaching 10,000 req/s needs roughly **40–50 API cores for cache-hit reads** and database read replicas for uncached reads; that extrapolation is **unvalidated** and needs a staging run (§6).

## 1. Environment (identical for every run)
| | |
|---|---|
| Host | **one** VM: 4 vCPU Intel Xeon @ 2.10 GHz, 16 GB RAM, Linux 6.18. **k6, the API, PostgreSQL and Redis all share it** (k6 itself consumes ~1 core), so every number is pessimistic for a real deployment and is not a capacity claim |
| PostgreSQL | 16.15, local, `max_connections=200`, `shared_buffers=128MB`, `work_mem=4MB`, `synchronous_commit=on`, `fsync=on`; no replica, no PgBouncer |
| Redis | local, default config |
| API | `uvicorn --workers N` (N = 3 unless stated), pool 10 per process (+0 overflow), admission slots 10/process, thread pool 14, rate limiting off |
| Load generator | k6 v0.54.0, **open-loop** `constant-arrival-rate`, 24 logged-in sessions of one administrator (class A–G scenarios in `tests/load/scenarios/classes.js`), 12 s per point (30 s warm-up/ramp not used: arrival rate starts immediately), one API configuration per `run_classes.sh` invocation |
| Dataset | a copy of the demo database: DB 23 MB, 262 sales orders, 474 stock-ledger rows, 462 journal entries, 2,554 audit rows, 148 tables. **Small versus a real plant; behaviour at production volume is unmeasured** |
| Commits | baseline `23ef16bf05` (before the performance changes); after `f38ef22` (+ later doc-only commits) |
| Not captured | per-process CPU/RSS, PostgreSQL CPU, pool-wait and lock-wait time per run (the earlier milestone's `harness.py` records these; `run_classes.sh` does not). Host-level saturation is inferred from throughput plateauing with rising latency |

"target" is the arrival rate the generator tried to sustain (iterations/s; class E issues 2 requests per iteration); "achieved" is requests/s over the whole run including login setup, so it under-reads slightly at low rates. "unexpected" counts non-2xx (E/F accept 409 as a business outcome).

## 2. Latency objectives used to judge each class
Read p95 ≤ 500 ms · dashboard p95 ≤ 500 ms (cached) · normal write p95 ≤ 1,000 ms · hot-stock, production-style and financial postings p95 ≤ 2,000 ms · unexpected failures < 0.1 %. A point "passes" only if it meets both.

## 3. Before and after, per class (3 API workers)
| Class | Before (`23ef16b`) | After (`f38ef22`) | Passing ceiling after |
|---|---|---|---|
| **A** cacheable reference reads | 100/s p95 53 ms · 200/s p95 61 ms · **400/s p95 3,214 ms (saturated, 251 achieved)** | 100/s p95 10 · 250/s p95 9 · **500/s p95 26 ms** · 750/s p95 509 ms; separate sweep run: 900/s target → 788 achieved, p95 183 ms, 0 failures | **≈ 500–790 req/s** (run-to-run spread ±25 %) |
| **B** uncached DB reads | 100/s p95 28 · 200/s p95 77 · 400/s p95 1,891 ms | 100/s p95 17 · 250/s p95 36 · 500/s p95 1,672 ms | **≈ 250–300 req/s** (DB/ORM bound: ≈ 8 ms CPU per request; session cache removed one query) |
| **C** dashboards | 100/s p95 418 ms · **250/s p95 2,601 ms** | 100/s p95 12 · 250/s p95 14 · 500/s p95 41 · **750/s p95 459 ms** | **≈ 600 req/s** (15 s cache with `meta.cached/generated_at`; ≈ 5× before) |
| **D** normal writes (`POST /crm/leads`) | 100/s p95 32 · 250/s p95 164 ms | 100/s p95 21 · 250/s p95 43 · 500/s p95 1,175 ms | **≈ 300 req/s** (session cache cut latency; ceiling is write CPU + commit) |
| **E** hot-stock reserve + release (2 requests per iteration) | 100/s p95 115 · 250/s p95 3,645 ms | 100/s p95 58 · 250/s p95 2,879 ms | **≈ 100–150 iterations/s** (contended row lock; correctness is the point, see `test_concurrency.py`) |
| **F** production-style postings (stock issues) | 100/s p95 33 · 250/s p95 639 ms | 100/s p95 33 · 250/s p95 216 ms | **≈ 210 req/s** |
| **G** financial postings (manual journals) | 100/s p95 40 · 250/s p95 718 ms | 100/s p95 47 · 250/s p95 1,338 ms | **≈ 190 req/s**. *No improvement; the single-sample difference is within run noise, but nothing here claims G got faster* |
| **H** background jobs | not re-measured: **no RabbitMQ in this sandbox**. The earlier milestone's recovery run (10,000 jobs, 3 worker kills + a publisher kill: 0 lost, 0 duplicated) stands as historical evidence only | | |

Failure rates: 0 unexpected responses in every passing point. Beyond each ceiling the system queues first (latency climbs, throughput plateaus) and then sheds with `503 + Retry-After` after 5 s (class E/F/G at ≥ 500/s: 4–36 % shed). No pool exhaustion, no deadlocks and no integrity violation was observed (the earlier milestone's integrity runs are the evidence for that; `run_classes.sh` does not run the integrity check).

## 4. Worker-count sweep (classes A and B; same hardware, k6 sharing the CPUs)
| API workers | A @ 600/s | A @ 900/s | B @ 300/s | B @ 600/s |
|---|---|---|---|---|
| 2 | 503 achieved, p95 252 ms | 546 achieved, **p95 2,422 ms (saturated)** | 249, p95 620 ms | 275, **p95 5,087 ms, 16 % shed** |
| 3 | 515, p95 20 ms | 788, p95 183 ms | 264, p95 80 ms | 439, p95 1,411 ms |
| 4 | 523, p95 18 ms | 785, p95 59 ms | 253, p95 194 ms | 473, p95 949 ms |
On 4 vCPUs shared with the load generator, **3 workers is the sensible setting**; the 4th adds little for A and trades CPU with k6. Rule for staging: processes = cores reserved for the API; verify with this sweep.

## 5. Sustained and failure drills (sandbox-scale)
* **5-minute sustained run**, class A at 400/s, 3 workers: 120,026 requests, **0 failures**, p50 9 ms, p95 105 ms, p99 413 ms. This is **not** the 30-minute target run or the 2-hour soak, and memory was not sampled.
* **Redis stopped for 8 s of a 30 s run** at 300/s: **0 failed requests** (all caches bypass to the database), but p95 rose to 1.37 s while Redis was down and the cache was off, then recovered. Expected behaviour of the fail-safe.
* **API restart** (3 workers down about 6 s of a 30 s run, single node): 19.6 % of requests failed with connection errors **while the node was down**, p95 88 ms otherwise. With two or more replicas behind a load balancer this is masked; a single node cannot mask it. Graceful shutdown/health checks exist; a rolling restart was not exercised.
* Not run: database restart/failover, connection-exhaustion drill, 2-hour soak, sudden-spike drill at fleet scale, multi-node runs.

## 6. Reading the numbers toward 10,000 req/s (unvalidated reasoning)
* Class A at ≈ 500–790 req/s used ≈ 3 cores of API workers ⇒ ≈ **170–260 req/s per API core**. Linear scaling would need **≈ 40–60 API cores** (≈ 10–15 four-vCPU nodes) for 10,000 req/s of cache-hit reads, plus a Redis that can serve ≈ 50 version reads per second per process (the 20 ms memo bounds it) and an LB.
* Classes B/D/F/G are database- and CPU-bound at ≈ 90–120 req/s per core; 10,000 req/s of such traffic is **not** a realistic single-primary target. The honest target for this architecture is: **mostly cache-hit reads at 10k aggregate, uncached reads and postings in the low thousands**, with read replicas for B.
* None of this was measured at that size. A staging proof needs ≥ 12 API cores, a dedicated PostgreSQL host, a separate load-generator host, a 30-minute hold at the target and a 2-hour soak, with CPU/RSS/pool/lock metrics captured per run.

## 7. Defects and decisions found while measuring
* The auth path (session query + permission epoch + Redis round trips) was the cheapest thing to remove: **−1 DB query and −2 Redis round trips per request**; the 20 ms Redis memo alone removed ≈ 29 % of busy CPU in the `py-spy` profile.
* A regression found by the full test run: a 15 s dashboard cache made a test see stale figures. Fixed by an explicit `?fresh=true` on both dashboard endpoints (and an autouse fixture that clears the response cache between tests); real users see the age of the figures on screen.
* Rejected / not done: disabling prepared statements (−3 %, earlier milestone), PgBouncer and read replicas (not measured here), materialised views (no dashboard query was the bottleneck once cached).

Raw k6 summaries: `tests/load/results/classes-*/` (one JSON per class and rate, `results.txt`, `meta.txt`). Reproduce: `tests/load/run_classes.sh <label> <workers> "<classes>" "<rates>" [duration]`; drills: `tests/load/resilience.sh`.
