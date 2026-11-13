"""Start / stop the demo stack without `make` (Windows-friendly):  python -m scripts.demo_stack up|down|status

up    API (2 workers) :8101, outbox publisher, Celery worker, Live Mode worker, and the web app :3101 if it has been built (logs in ./logs, pids in ./logs/stack.json)
down  stop what `up` started
Environment is forced to the demo settings (MINTREE_ENVIRONMENT=demo, demo database, isolated RabbitMQ vhost 'demo', MINTREE_DEMO_LIVE=true) unless already set."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND, LOGS = ROOT / "backend", ROOT / "logs"
PIDS = LOGS / "stack.json"
API_PORT, WEB_PORT = int(os.environ.get("API_PORT", 8101)), int(os.environ.get("WEB_PORT", 3101))


def env() -> dict:
    e = dict(os.environ)
    e.setdefault("MINTREE_ENVIRONMENT", "demo")
    e.setdefault("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_demo")
    e.setdefault("MINTREE_BROKER_URL", "amqp://guest:guest@127.0.0.1:5672/demo")
    e.setdefault("MINTREE_DEMO_LIVE", "true")
    e.setdefault("MINTREE_EINVOICE_MODE", "sandbox")          # a real IRN computation, clearly marked SANDBOX; registers nothing
    return e


def spawn(name: str, args: list[str], cwd: Path, extra: dict | None = None) -> int:
    LOGS.mkdir(exist_ok=True)
    flags = {"creationflags": 0x00000200 | 0x00000008} if os.name == "nt" else {"start_new_session": True}       # new process group, detached
    p = subprocess.Popen(args, cwd=cwd, env={**env(), **(extra or {})}, stdout=open(LOGS / f"{name}.log", "w"), stderr=subprocess.STDOUT, **flags)
    return p.pid


def up() -> None:
    py = sys.executable
    pids = {
        "api": spawn("api", [py, "-m", "uvicorn", "app.main:app", "--port", str(API_PORT), "--workers", os.environ.get("API_WORKERS", "1" if os.name == "nt" else "2"), "--log-level", "warning"], BACKEND),
        "publisher": spawn("publisher", [py, "-m", "app.workers.outbox_publisher"], BACKEND),
        "worker": spawn("worker", [py, "-m", "celery", "-A", "app.workers.celery_app", "worker", "-Q", "default,notifications,integrations,imports,reports,documents", "-c", "2", "-l", "warning", "-P", "solo" if os.name == "nt" else "prefork"], BACKEND),
        "live": spawn("live", [py, "-m", "app.demo_live"], BACKEND),
    }
    dist = ROOT / "frontend" / ".next-e2e-demo"
    if dist.exists():
        npx = "npx.cmd" if os.name == "nt" else "npx"
        pids["web"] = spawn("web", [npx, "next", "start", "-p", str(WEB_PORT)], ROOT / "frontend", {"BACKEND_URL": f"http://127.0.0.1:{API_PORT}", "NEXT_DIST_DIR": ".next-e2e-demo"})
    else:
        print("web app not built: cd frontend && BACKEND_URL=http://127.0.0.1:8101 NEXT_DIST_DIR=.next-e2e-demo npm run build")
    PIDS.write_text(json.dumps(pids))
    print("started:", pids, f"\nAPI http://127.0.0.1:{API_PORT}  web http://localhost:{WEB_PORT}  logs {LOGS}")


def down() -> None:
    if not PIDS.exists():
        print("nothing recorded")
        return
    for name, pid in json.loads(PIDS.read_text()).items():
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
        else:
            try:
                os.killpg(pid, 15)
            except ProcessLookupError:
                pass
        print("stopped", name, pid)
    PIDS.unlink()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    {"up": up, "down": down}.get(cmd, lambda: sys.exit(__doc__))()
