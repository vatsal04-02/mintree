# Performance Baseline

> ## Verdict (read this first)
> **The 1,000-concurrent-operation target is NOT demonstrated.** It has not been run at its specified size/duration, and
> it has not been run on a staging environment. On the shared 4-vCPU sandbox used here, 1,000 clients get 0 failures and
> intact data, but **p95 ≈ 3.3 s against a 0.5 s target** — the system queues rather than fails. The numbers below are
> *defect-finding and bottleneck-ranking measurements*, not capacity proof (`LOAD_TEST_PLAN.md`: "A developer laptop benchmark
> is not proof of production capacity.").
>
> What *is* established: two critical defects in the Phase 0 backend were found and fixed (it collapsed at 100 clients);
> every data-integrity invariant held in every run; failure under overload is bounded and graceful; recovery from killed
> workers loses and duplicates nothing; and the cost per request, hence the hardware needed, is now measured.

## 1. Environment (identical for every run unless stated)
| | |
|---|---|
| Host | 1 VM, **4 vCPU Intel Xeon @ 2.10 GHz, 16 GB RAM**, Linux 6.18 — **k6, API workers, PostgreSQL, RabbitMQ, Redis, Celery all on this one machine** |
| PostgreSQL | 16.15, local; `max_connections=100`, `shared_buffers=128MB`, `work_mem=4MB`, `synchronous_commit=on`, `fsync=on`, default autovacuum; no `pg_stat_statements` |
| API | uvicorn × **3 worker processes**, per-process DB pool 10 (+0 overflow), admission slots 10/process (=30 in flight), wait limit 5 s |
| Workers | 1 outbox publisher + 1 Celery prefork worker (-c 4, pool 3/process), RabbitMQ 3.12.1 (durable queues, publisher confirms), Redis 7 |
| Client | k6 v0.54, `THINK_MIN/MAX` = 0.5 / 2.5 s (mean 1.5 s) unless stated, 200 pooled logged-in users |
| Dataset (at start of run F) | 20,302 items (300 with 1 M-unit released stock + hot items) · 500 suppliers · 201 users · **12,417 POs / goods receipts · 59,136 ledger rows · 97,877 idempotency keys · 69,508 audit rows · 37,251 outbox events · DB 176 MB**. Small versus a real plant's history; the effect of table growth is *not* measured |
| Workload | `tests/load/lib/workload.js` default mix (≈ 51 % reads, 26 % inventory postings, 6 % PO→GR→QC chains, 3 % master writes; Accounts/Integrations **0 %** — modules do not exist; "production"/"dispatch" are inventory-endpoint proxies) |
| Sizes | **Reduced** durations for B, C, D, F, G (sandbox). A (smoke) ran at its full spec: 100 VUs × 10 min |

Per-run metadata (commit SHA, PostgreSQL settings, dataset row counts, workload env), k6 summary, Prometheus snapshots,
system CPU/RSS, `timeline.csv`, `integrity.json` are in `tests/load/results/<label>/`; the index is
`TEST_EXECUTION_REGISTER.md`. Runs marked "authz cache active" are flagged there (see §3.3).

## 2. Defects found by measurement (the most valuable result so far)

| # | Finding | Evidence | Remedy | After |
|---|---|---|---|---|
| 1 | **Lost writes**: commit ran after the HTTP response; a failing `COMMIT` returned 200 | experiment with a deferred-FK violation → 200 and 0 rows persisted | explicit unit of work commits before the response; `scope="function"` dependencies | regression test; same case → 500, nothing persisted |
| 2 | **Thread-pool / connection-pool deadlock → collapse at 100 clients** | original code, 100 VUs: **53.5 % failures, p95 60 s, ≈ 5 req/s**; `pg_stat_activity` all `idle in transaction`; `py-spy`: 10 threads blocked in pool checkout. *A first fix (cap threads ≤ connections) did not work* — the stack dump showed the same state | per-request **admission control** (≤ pool capacity in flight, async queue, 503 beyond 5 s) | same load: **0 % failures, ≈ 97 req/s/worker** |
| 3 | Lock-order violation: `ON CONFLICT DO UPDATE` locked balances in call order | live opposite-direction transfers: 12 transactions blocked, 1 s deadlock-detector stalls, timeouts | resolve ids without locking, then lock in the global order | 800 opposite transfers: 0 deadlocks |
| 4 | Batch↔balance deadlock: QC locked the batch `FOR UPDATE`; the ledger FK takes `KEY SHARE` | mixed workload: `tx failed after 4 attempts: deadlock` | `FOR NO KEY UPDATE` | 600 mixed ops: 0 deadlocks, 111 s → 27 s |
| 5 | **A blocking log sink stalled request threads while they held row locks** | `py-spy`: 11 threads inside the slow-tx log call; server stderr was an unread pipe | bounded, drop-on-overflow queue + writer thread; slow-tx logs rate-limited | no stalls; `log_records_dropped` metric |
| 6 | Qty double-counted on PO lines | first functional test; **caught by the DB `no_over_receipt` CHECK** | removed the duplicate write | — |
| 7 | 4 DB queries per authenticated request = **34 % of server CPU** (profile) | `py-spy record` under load | 1 joined query + Redis-epoch permission cache | §3.3 |

## 3. Results

### 3.1 Phase 0 code vs. fixed code — 100 clients, closed loop, 45 s, 1 worker (the collapse)
| | requests/s | failures | p95 |
|---|---|---|---|
| Original (`46c82cc`) | 5.2 | **53.5 %** | 60 s (client timeout) |
| After defect #2 fix | ≈ 97 | 0 % | 0.98 s (closed-loop queueing) |

### 3.2 Scenario results (3 API workers; "steady" = median req/s after ramp)
| Scenario (as run) | Code | avg / steady req/s | read p95 | write p95 | posting p95 | system failures | integrity | verdict vs criteria |
|---|---|---|---|---|---|---|---|---|
| **A** smoke, 100 clients, **10 min (full spec)** | original | 84 / 87 | 261 ms | 318 ms | 432 ms | **0** | **PASS** | **meets all latency/failure criteria** (read ≤ 500, write ≤ 1000/p99 ≤ 3000, posting ≤ 2000) |
| **B** progressive 250→500→750→1000 (20 s ramps, 60 s holds) | original | 200 / 216 | 3,844 ms (whole run) | 3,871 | 3,957 | 0 | PASS | latency criteria crossed from ≈ 500 clients; ceiling reached at ≈ 250 clients (§3.4) |
| **C** target, **1,000 clients, 3 min hold (reduced; spec 30 min)** | authz cache* | 241 / 267 | 3,263 ms | 3,292 | 3,378 | 0 | PASS | **NOT MET** (needs ≈ 2× throughput; queueing) |
| **D** spike 100→1000 in 30 s, 2 min hold (reduced) | authz cache* | 203 / 270 | 3,197 ms | 3,256 | 3,351 | 0 | PASS | not met; no errors during the spike |
| **E** hot stock, 200 clients on ONE balance/batch, 60 s | committed HEAD (p2) | 396 | — | **1,009 ms** (p99 1,482) | — | 0 | **PASS** | at the 1 s edge; 25,828 expected 409s; **nothing oversold** |
| **F** stress 1,000→1,250→1,500 (90 s holds) | authz cache* | 255 / 268 | n/a (thresholds off) | n/a | n/a | **0.2 %** = 241 shed 503s, all at ≈ 1,500 | PASS | graceful, see §3.4 |
| **G** soak-style, 400 clients, 15 min (reduced; spec 1,000 × 2 h) | HEAD | 254 / 278 | 757 ms | 784 | 871 | 0 | PASS | flat for 15 min (§3.5) — *memory not measured in this run (sampler bug)* |
| **G′** soak-style, **300 clients**, 8 min after ramp (reduced; fixed memory sampler) | HEAD | 221 / 264 | **243 ms** | **277 ms** | **361 ms** | **0** | **PASS** | **meets all latency/failure criteria** at 300 clients; k6 thresholds all passed |
| **H** worker recovery, **10,000 jobs**, SIGKILL 2 workers + the publisher | HEAD | 10,000 jobs in 26 s incl. kills | — | — | — | **0 lost, 0 duplicated** | PASS | 100 events republished after the publisher crash and de-duplicated; all queues (incl. dead-letter) empty |

\* *authz cache active*: these runs started after I edited the auth code in the working tree mid-suite; they are valid
correctness/behaviour results but **not pre-optimisation baselines**. The controlled comparison is §3.3.

### 3.3 Controlled A/B of the authentication optimisation (identical load, interleaved, 3 workers)
| | req/s (steady) | read p95 | write p95 | posting p95 |
|---|---|---|---|---|
| 250 clients — before (`aac5833`) | 218 | 251 ms | 287 ms | 377 ms |
| 250 clients — **after** | 232 | **132 ms (−47 %)** | **159 ms (−45 %)** | **242 ms (−36 %)** |
| 1,000 clients — before | 215 | 4,230 ms | 4,268 ms | 4,374 ms |
| 1,000 clients — **after** | **271 (+26 %)** | 3,349 ms | 3,387 ms | 3,444 ms |

Evaluated and **rejected** (1 worker, closed loop, interleaved ×2): disabling psycopg server-side prepare (−3 %), disabling
`pool_pre_ping` (+3.5 %, within noise, and it removes stale-connection protection). Config switches remain, defaults unchanged.

### 3.4 Capacity ceiling and degradation pattern
* **Ceiling ≈ 270 req/s** for this mix on this box (≈ 96 req/s per fully-busy Python worker ⇒ ≈ 10 ms CPU/request). Reached at
  ≈ 250 clients; more clients add **no throughput**, only queue (admission waiting grows 7 → ≈ 1,150).
* Bottleneck: **Python CPU** (host ≈ 80 % busy at saturation; PostgreSQL has 1–3 active backends — `pg_stat_activity`; no pool timeouts; no lock waits; 0 retries; the `py-spy` profile shows the time in the request path, not in waiting on the DB).
* Latency follows Little's law: `R ≈ N/X − Z`. With X ≈ 270, N = 1,000, Z = 1.5 s ⇒ ≈ 2.2 s mean, p95 ≈ 3.3 s (observed).
* **Overload behaviour (F):** ≈ 1,000 and ≈ 1,250 clients — queue only, **0 shed**; ≈ 1,500 clients — waits pass the 5 s limit and
  **241 requests are shed with `503 + Retry-After`**; throughput stays flat (264–278 req/s); no pool exhaustion, no deadlocks, no
  integrity violation. Degradation is bounded and graceful, not a collapse.
* **Planning model (unvalidated):** to serve 1,000 clients with ≈ 1.5 s think time at ≤ 0.25 s response needs
  X ≈ N/(Z+R) ≈ **570 req/s ≈ 2.1× the sandbox ceiling ≈ 6 busy API cores**, plus DB and worker cores (PostgreSQL ≈ 0.4 core per busy API worker in one early `top` snapshot ⇒ ≈ 2 cores *extrapolated*, not measured). Connection budget for that:
  6–8 API workers × 10 + workers ≈ 100 connections → set `max_connections ≥ 200` (or evaluate PgBouncer). **This must be verified on staging.**

### 3.5 Soak behaviour (sandbox-reduced)
G (400 clients, 15 min, after ramp): first / middle / last third — req/s **280 / 274 / 283**, p95 bucket **1 s / 1 s / 1 s**,
admission waiting 79 / 87 / 85, pool checked out 17 / 19.5 / 19, outbox backlog 5 / 6 / 6, host CPU 83 / 82 / 84 %;
0 retries, 0 deadlocks, 0 shed, 0 pool timeouts. No drift in the quantities measured. *The memory column of that run was 0 (the sampler matched only the supervisor process); it was fixed and G′ repeats a shorter soak with memory measured.* (An interrupted G′ attempt — the sandbox worker restarted mid-run — was discarded and never recorded.)

G′ (300 clients, 8 min after a 3-minute ramp, corrected sampler walking the whole process tree), first / middle / last third:
req/s **265 / 262 / 264**; admission waiting 3.5 / 8 / 8; pool checked out 16 / 17 / 13; outbox backlog 6 / 4 / 5; host CPU 80.2 / 80.3 / 80.5 %;
**API process-tree RSS 400.5 / 403 / 404 MB** (it rose 336 → 401 MB during warm-up, then plateaued: no leak visible in 8 min);
Celery + publisher RSS constant at 528 MB; 0 retries, 0 deadlocks, 0 shed, 0 pool timeouts, 0 5xx. The p95 histogram bucket moved
375 → 500 ms between the first and middle third and then stayed — one bucket step, not a trend; **8 minutes is far too short to
conclude "no degradation"**. A 2-hour soak at 1,000 clients on staging remains required.

## 4. Acceptance criteria — status
| Criterion | Status |
|---|---|
| Read p95 ≤ 500 ms / write p95 ≤ 1 s / posting p95 ≤ 2 s / write p99 ≤ 3 s | **Met at 100 clients (A) and 250 clients (A/B); NOT met at ≥ 500 clients on this host** |
| 5xx < 0.1 % / unexpected failures < 0.5 % | Met everywhere except F at ≈ 1,500 clients (0.2 %, all deliberate load-shedding 503s) |
| Queue wait p95 ≤ 10 s | **Met**: p95 ≤ 0.05 s (histogram bucket edge) in A, B, C, D, F and G; the outbox backlog was 0 at the end of every run; H drained 10,000 jobs in 26 s |
| No pool exhaustion | **Met** (0 pool timeouts in every run) |
| No unexplained deadlocks | **Met** (0 `pg_stat_database.deadlocks` in all runs after the fixes; the earlier ones were found and fixed) |
| No lost / duplicate business effects | **Met** in every integrity check and in H |
| No negative available stock | **Met** (DB CHECK + integrity check in every run; hot-stock E: 25.8 k conflicts, nothing oversold) |
| No unbalanced accounting postings | **Not demonstrated under load.** All earlier runs predate the Accounts module (no journals were posted). The check is now real and passes in the functional/E2E suites; it must be re-run after load scenarios that post journals |
| **1,000 concurrent operations, 30 min, on staging** | **NOT DEMONSTRATED** |

## 5. Limitations of these measurements
1. One shared 4-vCPU host: k6, API, PostgreSQL, RabbitMQ and Celery compete; absolute numbers are pessimistic for a real deployment and the CPU split is not production-like. **Only host-level CPU % is trustworthy in runs before G′:** the original sampler recreated process objects every interval, so per-process CPU (k6, PostgreSQL, API) read 0 or garbage — k6's own CPU share was therefore never measured.
2. Reduced durations for B/C/D/F/G; **no 2-hour soak, no 30-minute target run**.
3. Small dataset (176 MB, 59 k ledger rows); behaviour as tables grow to production volume (index depth, vacuum, `count(*)` per page, `OFFSET` pagination, audit-log growth) is **unmeasured**.
4. App-measured query/transaction time is inflated on a CPU-saturated host (6 ms per PK lookup vs ≈ 0.2 ms in PostgreSQL); `pg_stat_statements` was not enabled.
5. Production/dispatch are proxies; Accounts, marketplace and other modules are not modelled because they do not exist.
6. Think-time distribution (0.5–2.5 s uniform) is an assumption; real Mintree behaviour will differ.

## 6. To demonstrate the target (what staging must provide)
Separate hosts for API (≥ 8 vCPU), PostgreSQL (with `pg_stat_statements`, tuned memory, `max_connections` per §3.4), RabbitMQ/Redis and
**k6 on its own machine**; a realistic data volume; then `harness.py --scenario C_target.js` (30 min) → `check_integrity`; then B, D, F and the
2 h soak G. Only then may the capacity claim be made.
