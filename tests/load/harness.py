#!/usr/bin/env python3
"""Reproducible load-test runner. Run with the backend venv:

  backend/.venv/bin/python tests/load/harness.py --scenario baseline_phase0.js --label my-run \
      --workers 4 --env VUS=100 --env DURATION=30s

What it records (results/<label>/): meta.json (date, commit SHA, machine, PostgreSQL settings, dataset sizes,
workload env, API config), k6-summary.json, metrics-start/end.prom + periodic scrapes + system.csv,
api.log (structured JSON), integrity.json (when --integrity), and appends one line to results/register.jsonl,
from which docs/TEST_EXECUTION_REGISTER.md is rendered. It refuses to run against a database that is not
named *_perf or *_staging.
"""
import argparse, csv, datetime as dt, json, os, platform, shutil, signal, subprocess, sys, tempfile, threading, time, urllib.request
from pathlib import Path

import psutil
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
BACKEND = ROOT / "backend"


def sh(*cmd, cwd=None) -> str:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True).stdout.strip()


def http_get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read().decode()


def machine() -> dict:
    cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), platform.processor())
    return {"cpu_model": cpu, "logical_cpus": os.cpu_count(), "mem_total_mb": psutil.virtual_memory().total // 2**20,
            "kernel": platform.release(), "python": platform.python_version(), "k6": sh("k6", "version").split("\n")[0],
            "note": "k6, API, PostgreSQL and the harness share this machine unless stated otherwise"}


def pg_info(engine) -> dict:
    with engine.connect() as c:
        s = {n: c.execute(text(f"SHOW {n}")).scalar() for n in
             ("max_connections", "shared_buffers", "work_mem", "effective_cache_size", "synchronous_commit", "fsync",
              "wal_level", "random_page_cost", "max_wal_size")}
        s["version"] = c.execute(text("SHOW server_version")).scalar()
        tables = ["users", "sessions", "items", "suppliers", "audit_logs", "documents", "stock_ledger", "stock_balances",
                  "stock_reservations", "purchase_orders", "goods_receipts", "outbox_events", "idempotency_keys"]
        counts = {}
        for t in tables:
            try:
                counts[t] = c.execute(text(f"SELECT count(*) FROM {t}")).scalar()
            except Exception:
                c.rollback()
        s["dataset_rows"] = counts
        s["db_size_mb"] = c.execute(text("SELECT pg_database_size(current_database())/1024/1024")).scalar()
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--db-url", default=os.environ.get("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_perf"))
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--code-dir", default=str(BACKEND), help="backend dir to run (e.g. a git worktree of an older commit)")
    ap.add_argument("--env", action="append", default=[], help="K=V passed to k6 (-e)")
    ap.add_argument("--api-env", action="append", default=[], help="K=V passed to the API process")
    ap.add_argument("--scrape-interval", type=float, default=10)
    ap.add_argument("--integrity", action="store_true", help="run the DB integrity checker after the test")
    ap.add_argument("--no-api", action="store_true", help="API already running (staging); do not start one")
    a = ap.parse_args()

    dbname = make_url(a.db_url).database or ""
    if not dbname.endswith(("_perf", "_staging")):
        sys.exit(f"refusing: database '{dbname}' must end in _perf or _staging")
    out = HERE / "results" / a.label
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    base = f"http://127.0.0.1:{a.port}"
    engine = create_engine(a.db_url, pool_size=1)

    api = None
    if not a.no_api:
        try:
            http_get(f"{base}/api/health", 1)
            sys.exit(f"port {a.port} already serves an API; refusing (stale processes invalidated earlier runs)")
        except OSError:
            pass
        env = dict(os.environ, MINTREE_DATABASE_URL=a.db_url, MINTREE_ENVIRONMENT="perf",
                   PROMETHEUS_MULTIPROC_DIR=tempfile.mkdtemp(prefix="prom-"), PYTHONPATH=a.code_dir)
        env.update(kv.split("=", 1) for kv in a.api_env)
        log = open(out / "api.log", "w")
        api = subprocess.Popen([str(BACKEND / ".venv/bin/python"), "-m", "uvicorn", "app.main:app", "--port", str(a.port),
                                "--workers", str(a.workers), "--no-access-log", "--log-level", "warning"],
                               cwd=a.code_dir, env=env, stdout=log, stderr=log, start_new_session=True)
        for _ in range(60):
            try:
                http_get(f"{base}/api/health", 1); break
            except OSError:
                time.sleep(1)
        else:
            os.killpg(api.pid, signal.SIGKILL); sys.exit("API did not start; see api.log")

    meta = {"label": a.label, "scenario": a.scenario, "started_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "commit": sh("git", "rev-parse", "HEAD", cwd=a.code_dir), "dirty": bool(sh("git", "status", "--porcelain", cwd=a.code_dir)),
            "machine": machine(), "postgres": pg_info(engine), "api": {"workers": a.workers, "env": a.api_env, "code_dir": a.code_dir},
            "k6_env": a.env, "database": dbname}
    (out / "meta.json").write_text(json.dumps(meta, indent=2))

    def scrape(tag):
        try:
            (out / f"metrics-{tag}.prom").write_text(http_get(f"{base}/api/metrics"))
        except Exception as e:
            (out / f"metrics-{tag}.err").write_text(str(e))

    stop = threading.Event()

    def sampler():
        procs_csv = csv.writer(open(out / "system.csv", "w", newline=""))
        procs_csv.writerow(["t", "cpu_total_pct", "mem_used_mb", "api_cpu_pct", "api_rss_mb", "postgres_cpu_pct", "k6_cpu_pct"])
        i = 0
        while not stop.wait(a.scrape_interval):
            i += 1
            scrape(f"t{i:04d}")
            api_p = [p for p in psutil.process_iter(["cmdline", "cpu_percent", "memory_info"]) if "uvicorn" in " ".join(p.info["cmdline"] or [])]
            pg_p = [p for p in psutil.process_iter(["name", "cpu_percent"]) if p.info["name"] == "postgres"]
            k6_p = [p for p in psutil.process_iter(["name", "cpu_percent"]) if p.info["name"] == "k6"]
            procs_csv.writerow([i * a.scrape_interval, psutil.cpu_percent(), psutil.virtual_memory().used // 2**20,
                                sum(p.info.get("cpu_percent") or 0 for p in api_p), sum((p.info.get("memory_info").rss if p.info.get("memory_info") else 0) for p in api_p) // 2**20,
                                sum(p.info.get("cpu_percent") or 0 for p in pg_p), sum(p.info.get("cpu_percent") or 0 for p in k6_p)])

    scrape("start")
    th = threading.Thread(target=sampler, daemon=True); th.start()
    kcmd = ["k6", "run", "-q", "-e", f"BASE_URL={base}"] + [x for e in a.env for x in ("-e", e)] + \
           ["--summary-export", str(out / "k6-summary.json"), str(HERE / "scenarios" / a.scenario)]
    t0 = time.time()
    k = subprocess.run(kcmd, capture_output=True, text=True, cwd=HERE)
    wall = time.time() - t0
    (out / "k6.log").write_text(k.stdout + k.stderr)
    stop.set(); th.join(timeout=5)
    scrape("end")

    integrity = None
    if a.integrity:
        r = subprocess.run([str(BACKEND / ".venv/bin/python"), "-m", "scripts.check_integrity", "--json"], cwd=BACKEND,
                           env=dict(os.environ, MINTREE_DATABASE_URL=a.db_url, PYTHONPATH=str(BACKEND)), capture_output=True, text=True)
        try:
            integrity = json.loads(r.stdout)
        except Exception:
            integrity = {"ok": False, "error": (r.stdout + r.stderr)[-500:]}
        (out / "integrity.json").write_text(json.dumps(integrity, indent=2))

    if api:
        os.killpg(api.pid, signal.SIGTERM)
        try: api.wait(15)
        except subprocess.TimeoutExpired: os.killpg(api.pid, signal.SIGKILL)

    summ = json.loads((out / "k6-summary.json").read_text())["metrics"] if (out / "k6-summary.json").exists() else {}
    g = lambda m, f: summ.get(m, {}).get(f)
    row = {"label": a.label, "date": meta["started_at"], "commit": meta["commit"][:10] + ("+dirty" if meta["dirty"] else ""),
           "scenario": a.scenario, "env": a.env, "workers": a.workers, "wall_s": round(wall, 1),
           "req_per_s": g("http_reqs", "rate"), "requests": g("http_reqs", "count"),
           "read_p95_ms": g("http_req_duration{kind:read}", "p(95)"), "read_p99_ms": g("http_req_duration{kind:read}", "p(99)"),
           "write_p95_ms": g("http_req_duration{kind:write}", "p(95)"), "write_p99_ms": g("http_req_duration{kind:write}", "p(99)"),
           "posting_p95_ms": g("http_req_duration{kind:posting}", "p(95)"),
           "system_failure_rate": g("system_failures", "value"), "business_conflicts": g("business_conflicts", "count"),
           "k6_exit": k.returncode, "integrity_ok": None if integrity is None else integrity.get("ok"),
           "machine": f'{meta["machine"]["logical_cpus"]} vCPU / {meta["machine"]["mem_total_mb"]} MB (shared)',
           "dataset": meta["postgres"]["dataset_rows"], "dir": f"tests/load/results/{a.label}"}
    with open(HERE / "results" / "register.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    print(json.dumps({k: v for k, v in row.items() if k not in ("dataset", "machine", "dir", "env")}, indent=1))
    print(f"k6 exit={k.returncode} (non-zero = a threshold was crossed)   results: {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
