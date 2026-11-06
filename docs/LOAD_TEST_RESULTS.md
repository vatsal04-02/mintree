# Load Test Results

## 1. Status in one paragraph

**No new load results were produced in this work.** The build container is a shared 4-vCPU machine without Docker or RabbitMQ; running k6 and the API on the same host cannot show production capacity, and the brief forbids claiming it. The results that exist were produced earlier, on the same kind of sandbox, and are indexed (with commit SHA, parameters, integrity verdict and caveats) in `TEST_EXECUTION_REGISTER.md`, section 2, with per-run artefacts in `tests/load/results/`. Their reading is in `PERFORMANCE_BASELINE.md`.

## 2. What the existing results do show (and only that)

| Question | Answer (source) |
|---|---|
| Throughput of one API process | about 100 requests/s, CPU bound (`OPT-*` runs) |
| Throughput of 3-4 processes on the shared box | about 200-270 requests/s (runs `B`, `C`, `AB-*`, `G`) |
| Behaviour at 1,000 simulated clients (reduced duration) | 0 system failures, intact data, p95 about 3.3 s (target 0.5 s): queues rather than fails |
| Hot-stock contention | 200 clients on one batch: no overselling; business conflicts returned as 409 (run `E-hot-stock-p2`) |
| Worker crash recovery | 10,000 jobs, 4 kills: lost 0, duplicated 0 (`H-worker-recovery-p2`) |
| Data integrity after every run | PASS in every recorded run |

## 3. Workloads still to be written and run (required by the brief)

Each needs a k6 scenario in `tests/load/scenarios/`, a result row in the register, and a run on **dedicated staging hardware with documented CPU, RAM, database and storage specifications**.

| Workload | Why it is distinct | Status |
|---|---|---|
| Inventory writes / hot stock | contention on one balance | exists (`E-hot-stock`) |
| Procurement chain (PR -> PO -> receipt -> QC) | multi-document transaction | partly covered by the default mix |
| Production issue and completion | many ledger rows and valuation locks per call | **not written** |
| QC transitions | status and ledger blocked quantities | partly |
| Dispatch and invoicing (incl. partial shipments) | gapless invoice counter is a hot row | **not written for the new flow** |
| Accounting postings (journals, payments, allocations) | period and counter locks | **not written** |
| Marketplace imports and webhook bursts | idempotent bulk ingest | **not written** |
| Dashboard reads / large reports | many aggregates per request | **not written** |
| Mixed concurrent operations | realistic blend | exists (default mix, without the modules above) |

## 4. Acceptance record (to be filled from a staging run)

| Item | Value |
|---|---|
| Hardware (API / DB / broker / load generator separately) | _not run_ |
| Concurrency, duration, think time | _not run_ |
| Throughput, p50 / p95 / p99 | _not run_ |
| Errors, DB lock waits, queue depth, CPU / memory | _not run_ |
| Integrity verdict | _not run_ |

Do not enter numbers here from a development machine.
