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


def _hist_p95(path, name):
    """Upper bound (bucket edge) of the p95 of a Prometheus histogram summed over labels."""
    try:
        import re
        b = {}
        for line in open(path):
            m = re.match(rf'{name}_bucket\{{[^}}]*le="([^"]+)"[^}}]*\}} ([0-9.e+]+)', line)
            if m:
                b[m.group(1)] = b.get(m.group(1), 0) + float(m.group(2))
        if not b: return None
        tot = b.get("+Inf", 0)
        for le in sorted((k for k in b if k != "+Inf"), key=float):
            if tot and b[le] / tot >= 0.95: return float(le)
        return float("inf")
    except Exception:
        return None


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
    ap.add_argument("--no-async", action="store_true", help="do not start the outbox publisher / Celery worker")
    ap.add_argument("--celery-concurrency", type=int, default=4)
    ap.add_argument("--drain-timeout", type=float, default=180, help="seconds to wait for the outbox/queues to drain after k6")
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

    aux = []
    if api and not a.no_async:
        wenv = dict(env, MINTREE_DB_POOL_SIZE="3", MINTREE_ENVIRONMENT="perf")
        for name, cmd in (("publisher", [str(BACKEND / ".venv/bin/python"), "-m", "app.workers.outbox_publisher"]),
                          ("celery", [str(BACKEND / ".venv/bin/python"), "-m", "celery", "-A", "app.workers.celery_app", "worker", "-Q",
                                      "default,notifications", "-c", str(a.celery_concurrency), "-l", "warning", "--without-gossip",
                                      "--without-mingle", "-n", "perf@%h"])):
            aux.append(subprocess.Popen(cmd, cwd=a.code_dir, env=wenv, stdout=open(out / f"{name}.log", "w"), stderr=subprocess.STDOUT,
                                        start_new_session=True))
        time.sleep(4)

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
        """CPU/RSS per process TREE (uvicorn workers are spawned children; matching on the command line missed them)."""
        procs_csv = csv.writer(open(out / "system.csv", "w", newline=""))
        procs_csv.writerow(["t", "cpu_total_pct", "mem_used_mb", "api_cpu_pct", "api_rss_mb", "worker_cpu_pct", "worker_rss_mb", "postgres_cpu_pct", "k6_cpu_pct"])
        cache: dict[int, psutil.Process] = {}

        def tree(root_pid):
            cpu = rss = 0.0
            try:
                root = psutil.Process(root_pid)
                members = [root] + root.children(recursive=True)
            except psutil.Error:
                return 0.0, 0.0
            for m in members:
                p = cache.setdefault(m.pid, m)       # keep Process objects so cpu_percent has a baseline
                try:
                    cpu += p.cpu_percent(None); rss += p.memory_info().rss
                except psutil.Error:
                    pass
            return cpu, rss / 2**20

        def by_name(name):
            tot = 0.0
            for p in psutil.process_iter(["name"]):
                if p.info["name"] == name:
                    q = cache.setdefault(p.pid, p)
                    try: tot += q.cpu_percent(None)
                    except psutil.Error: pass
            return tot

        i = 0
        while not stop.wait(a.scrape_interval):
            i += 1
            scrape(f"t{i:04d}")
            ac, ar = tree(api.pid) if api else (0, 0)
            wc = wr = 0.0
            for pr in aux:
                c, r = tree(pr.pid); wc += c; wr += r
            procs_csv.writerow([i * a.scrape_interval, psutil.cpu_percent(), psutil.virtual_memory().used // 2**20, round(ac, 1), round(ar),
                                round(wc, 1), round(wr), round(by_name("postgres"), 1), round(by_name("k6"), 1)])

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

    drain = None
    if aux:
        t0d, backlog0 = time.time(), None
        with engine.connect() as c:
            backlog0 = c.execute(text("SELECT count(*) FROM outbox_events WHERE status IN ('pending','publishing')")).scalar()
        while time.time() - t0d < a.drain_timeout:
            with engine.connect() as c:
                left = c.execute(text("SELECT count(*) FROM outbox_events WHERE status IN ('pending','publishing')")).scalar()
            if left == 0:
                try:
                    import base64
                    req = urllib.request.Request("http://127.0.0.1:15672/api/queues", headers={
                        "Authorization": "Basic " + base64.b64encode(b"guest:guest").decode()})
                    q = json.loads(urllib.request.urlopen(req, timeout=5).read())
                    busy = sum(x.get("messages", 0) for x in q if x["name"] in ("default", "notifications"))
                except Exception as e:
                    busy = 1   # cannot prove the queues are empty -> keep waiting (never silently pass)
                    (out / "drain-warning.txt").write_text(f"RabbitMQ API unreadable: {e}")
                if busy == 0:
                    break
            time.sleep(1)
        drain = {"outbox_backlog_at_k6_end": backlog0, "drain_seconds": round(time.time() - t0d, 1), "drained": left == 0}
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

    for pr in aux:
        try: os.killpg(pr.pid, signal.SIGTERM)
        except ProcessLookupError: pass
    if api:
        os.killpg(api.pid, signal.SIGTERM)
        try: api.wait(15)
        except subprocess.TimeoutExpired: os.killpg(api.pid, signal.SIGKILL)

    try:   # compact per-interval timeline (versioned; the raw per-interval snapshots are not)
        tl = subprocess.run([sys.executable, str(HERE / "analyze_run.py"), a.label, "--csv"], capture_output=True, text=True)
        if tl.returncode == 0:
            (out / "timeline.csv").write_text(tl.stdout)
    except Exception:
        pass
    summ = json.loads((out / "k6-summary.json").read_text())["metrics"] if (out / "k6-summary.json").exists() else {}
    g = lambda m, f: summ.get(m, {}).get(f)
    row = {"label": a.label, "date": meta["started_at"], "commit": meta["commit"][:10] + ("+dirty" if meta["dirty"] else ""),
           "scenario": a.scenario, "env": a.env, "workers": a.workers, "wall_s": round(wall, 1),
           "req_per_s": g("http_reqs", "rate"), "requests": g("http_reqs", "count"),
           "read_p95_ms": g("http_req_duration{kind:read}", "p(95)"), "read_p99_ms": g("http_req_duration{kind:read}", "p(99)"),
           "write_p95_ms": g("http_req_duration{kind:write}", "p(95)"), "write_p99_ms": g("http_req_duration{kind:write}", "p(99)"),
           "posting_p95_ms": g("http_req_duration{kind:posting}", "p(95)"),
           "system_failure_rate": g("system_failures", "value"), "business_conflicts": g("business_conflicts", "count"),
           "async": drain, "queue_wait_p95_s": _hist_p95(out / "metrics-end.prom", "mintree_worker_queue_wait_seconds"),
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
