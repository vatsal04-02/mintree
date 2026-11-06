#!/usr/bin/env python3
"""H. Worker recovery: queue 10,000 background jobs, kill workers (and the publisher) mid-flight, restart them, and
prove processing recovers with NO lost and NO duplicated business effects.

  backend/.venv/bin/python tests/load/worker_recovery.py --label H-worker-recovery [--jobs 10000]

How the 10,000 jobs are created: 10,000 committed outbox events (type inventory.goods_receipt.posted), exactly as 10,000
goods-receipt transactions would have left them. Each event has one consumer (notify.goods_receipt_posted) whose
effect is one row in `notifications`. So "lost" = fewer than 10,000 notifications, "duplicated" = more than one per
event (also blocked by unique(event_id, kind)).

Chaos applied while the backlog drains: SIGKILL a Celery worker (mid-task, unacked messages must be redelivered)
three times, SIGKILL the outbox publisher once (rows stuck in 'publishing' must be reclaimed after the lease), and
restart each. Runs against a *_perf / *_staging database only.
"""
import argparse, json, os, random, signal, subprocess, sys, time, uuid
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent.parent / "backend"
PY = str(BACKEND / ".venv/bin/python")


def spawn(cmd, env, log):
    return subprocess.Popen(cmd, cwd=BACKEND, env=env, stdout=open(log, "a"), stderr=subprocess.STDOUT, start_new_session=True)


def kill9(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def rabbit(path):
    import base64, urllib.request
    req = urllib.request.Request(f"http://127.0.0.1:15672/api/{path}", headers={"Authorization": "Basic " + base64.b64encode(b"guest:guest").decode()})
    return json.loads(urllib.request.urlopen(req, timeout=5).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="H-worker-recovery")
    ap.add_argument("--jobs", type=int, default=10_000)
    ap.add_argument("--db-url", default=os.environ.get("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_perf"))
    ap.add_argument("--workers", type=int, default=2, help="Celery worker processes (each -c 4)")
    ap.add_argument("--kills", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=600)
    a = ap.parse_args()
    db = make_url(a.db_url).database or ""
    if not db.endswith(("_perf", "_staging")):
        sys.exit(f"refusing: database '{db}' must end in _perf or _staging")
    out = HERE / "results" / a.label
    out.mkdir(parents=True, exist_ok=True)
    for f in out.glob("*"):
        f.unlink()
    eng = create_engine(a.db_url)
    env = dict(os.environ, MINTREE_DATABASE_URL=a.db_url, MINTREE_DB_POOL_SIZE="3", MINTREE_OUTBOX_LEASE_S="15", PYTHONPATH=str(BACKEND),
               MINTREE_ENVIRONMENT="perf")
    tag = uuid.uuid4().hex[:8]

    # Start from a clean queue so the final queue-depth check is meaningful.
    for q in ("default", "notifications"):
        try:
            import base64, urllib.request
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:15672/api/queues/%2F/{q}/contents", method="DELETE",
                                   headers={"Authorization": "Basic " + base64.b64encode(b"guest:guest").decode()}), timeout=5)
        except Exception as e:
            print("warning: could not purge queue", q, e)
    with eng.begin() as c:
        c.execute(text("UPDATE outbox_events SET status='published', published_at=now() WHERE status <> 'published'"))
        before = {"notifications": c.execute(text("SELECT count(*) FROM notifications")).scalar(),
                  "processed": c.execute(text("SELECT count(*) FROM processed_events")).scalar(),
                  "failures": c.execute(text("SELECT count(*) FROM job_failures")).scalar()}
        c.execute(text("""INSERT INTO outbox_events(event_id, event_type, aggregate_type, aggregate_id, payload, request_id)
                          SELECT gen_random_uuid(), 'inventory.goods_receipt.posted', 'goods_receipt', g::text,
                                 jsonb_build_object('number', 'H-' || :t || '-' || g, 'batch', :t), 'recovery-' || :t
                            FROM generate_series(1, CAST(:n AS int)) g"""), {"n": a.jobs, "t": tag})
    print(f"queued {a.jobs} events (tag {tag}); starting publisher + {a.workers} workers")

    def start_worker(i):
        return spawn([PY, "-m", "celery", "-A", "app.workers.celery_app", "worker", "-Q", "default,notifications", "-c", "4", "-l", "warning",
                      "--without-gossip", "--without-mingle", "-n", f"rec{i}@%h"], env, out / f"worker{i}.log")

    pub = spawn([PY, "-m", "app.workers.outbox_publisher"], env, out / "publisher.log")
    workers = [start_worker(i) for i in range(a.workers)]
    t0, kills, events = time.time(), [], []
    kill_at = sorted(random.Random(1).uniform(0.15, 0.7) for _ in range(a.kills))   # as fractions of progress
    killed_pub = False

    def progress():
        with eng.connect() as c:
            return c.execute(text("SELECT count(*) FROM notifications WHERE body->>'batch' = :t"), {"t": tag}).scalar()

    done = 0
    while time.time() - t0 < a.timeout:
        done = progress()
        frac = done / a.jobs
        if kill_at and frac >= kill_at[0]:
            kill_at.pop(0)
            i = random.randrange(len(workers))
            kill9(workers[i])
            kills.append({"t": round(time.time() - t0, 1), "what": f"celery worker {i}", "progress": done})
            time.sleep(1.5)
            workers[i] = start_worker(i)
        if not killed_pub and frac >= 0.4:
            killed_pub = True
            kill9(pub)
            kills.append({"t": round(time.time() - t0, 1), "what": "outbox publisher", "progress": done})
            time.sleep(1.5)
            pub = spawn([PY, "-m", "app.workers.outbox_publisher"], env, out / "publisher2.log")
        if done >= a.jobs:
            break
        time.sleep(0.5)
    elapsed = time.time() - t0
    time.sleep(3)   # let in-flight acks / marks settle

    with eng.connect() as c:
        r = {
            "jobs": a.jobs, "elapsed_s": round(elapsed, 1), "chaos_events": kills,
            "notifications_for_batch": c.execute(text("SELECT count(*) FROM notifications WHERE body->>'batch' = :t"), {"t": tag}).scalar(),
            "distinct_events_with_notification": c.execute(text("SELECT count(DISTINCT event_id) FROM notifications WHERE body->>'batch' = :t"), {"t": tag}).scalar(),
            "duplicate_notifications": c.execute(text("SELECT count(*) FROM (SELECT event_id, kind FROM notifications WHERE body->>'batch' = :t GROUP BY 1,2 HAVING count(*) > 1) x"), {"t": tag}).scalar(),
            "processed_markers": c.execute(text("SELECT count(*) FROM processed_events p JOIN outbox_events o ON o.event_id = p.event_id WHERE o.request_id = :r"), {"r": f"recovery-{tag}"}).scalar(),
            "outbox_published": c.execute(text("SELECT count(*) FROM outbox_events WHERE request_id = :r AND status='published'"), {"r": f"recovery-{tag}"}).scalar(),
            "outbox_not_published": c.execute(text("SELECT count(*) FROM outbox_events WHERE request_id = :r AND status <> 'published'"), {"r": f"recovery-{tag}"}).scalar(),
            "outbox_max_attempts": c.execute(text("SELECT max(attempts) FROM outbox_events WHERE request_id = :r"), {"r": f"recovery-{tag}"}).scalar(),
            "republished_after_crash": c.execute(text("SELECT count(*) FROM outbox_events WHERE request_id = :r AND attempts > 1"), {"r": f"recovery-{tag}"}).scalar(),
            "new_job_failures": c.execute(text("SELECT count(*) FROM job_failures")).scalar() - before["failures"],
        }
    try:
        q = {x["name"]: x for x in rabbit("queues")}
        r["queues_after"] = {n: {"ready": q[n].get("messages_ready"), "unacked": q[n].get("messages_unacknowledged")} for n in ("default", "notifications", "dead_letter") if n in q}
    except Exception as e:
        r["queues_after"] = f"unavailable: {e}"
    r["lost"] = a.jobs - r["distinct_events_with_notification"]
    r["duplicated_effects"] = r["notifications_for_batch"] - r["distinct_events_with_notification"]
    r["ok"] = (r["lost"] == 0 and r["duplicated_effects"] == 0 and r["duplicate_notifications"] == 0 and r["outbox_not_published"] == 0
               and r["new_job_failures"] == 0 and r["processed_markers"] == a.jobs)
    (out / "result.json").write_text(json.dumps(r, indent=2))
    for p in workers + [pub]:
        kill9(p)
    with open(HERE / "results" / "register.jsonl", "a") as f:
        f.write(json.dumps({"label": a.label, "date": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), "scenario": "worker_recovery.py",
                            "commit": subprocess.run(["git", "rev-parse", "--short=10", "HEAD"], capture_output=True, text=True, cwd=BACKEND).stdout.strip(),
                            "jobs": a.jobs, "elapsed_s": r["elapsed_s"], "lost": r["lost"], "duplicated_effects": r["duplicated_effects"],
                            "chaos_events": len(kills), "ok": r["ok"], "dir": f"tests/load/results/{a.label}"}) + "\n")
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
