"""Load and soak driver for the demo API (docs/DEMO_RUNBOOK.md, "Load and soak").

    python tests/demo/load.py --base http://127.0.0.1:8101 --users 50 --minutes 15              # ramp over 60 s, then steady, 80% reads / 20% writes
    python tests/demo/load.py --base http://127.0.0.1:8101 --users 10 --minutes 60 --label soak   # soak: also reports the memory trend

Virtual users are real demo users (admin, buyer, sales, dispatch, accounts, stores, production) signed in once; each loops over the real demo flows with think time:
reads = overview and module dashboards, 50-row lists, record drawers, palette search; writes = marketplace orders, sales-order draft+confirm, customer payments on distinct open invoices,
purchase requests, CRM deals, mark-notification-read. Counted as ERRORS: HTTP 5xx, transport failures, timeouts. Business conflicts (409/422) are counted separately: a race on a business rule
is the system working, but they should be rare because every writer works on its own records.
Samples (every 10 s): API process RSS and CPU (psutil), database connections, so a leak or runaway shows as a trend. Writes a JSON report to tests/load/results/."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = os.environ.get("DEMO_PASSWORD", "Mintree-Demo#2026")
ROLES = ["admin", "admin", "buyer", "sales", "disp", "acct", "stores", "pm"]
READS = [("dashboard", "/api/analytics/management-summary"), ("dashboard", "/api/inventory/hub"), ("dashboard", "/api/production/dashboard"), ("dashboard", "/api/crm/dashboard"),
         ("dashboard", "/api/accounts/dashboard"), ("dashboard", "/api/ecommerce/omnihub"), ("list", "/api/dispatch/orders?page_size=50"), ("list", "/api/masters/customers?page_size=50&page=3"),
         ("list", "/api/masters/items?page_size=50"), ("list", "/api/inventory/stock?page_size=50"), ("list", "/api/ecommerce/external-orders?page_size=50"), ("list", "/api/procurement/purchase-orders?page_size=50"),
         ("list", "/api/production/orders?page_size=25"), ("list", "/api/notifications?page_size=50"), ("search", "/api/masters/items?q=oud&page_size=8"), ("search", "/api/masters/customers?q=beauty&page_size=8"),
         ("search", "/api/masters/suppliers?q=chem&page_size=8"), ("detail", "DETAIL")]


class Stats:
    def __init__(self):
        self.lat: dict[str, list[float]] = {}
        self.errors: list[tuple[str, int | str]] = []
        self.conflicts = 0
        self.slow = 0
        self.total = 0
        self.t0 = time.time()
        self.samples: list[dict] = []

    def rec(self, cls: str, ms: float, status: int | str):
        self.total += 1
        if isinstance(status, str) or status >= 500:
            self.errors.append((cls, status))
        elif status in (409, 422, 429):
            self.conflicts += 1
        if ms > 2000:
            self.slow += 1
        self.lat.setdefault(cls, []).append(ms)


def pct(xs, p):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))], 1) if xs else 0.0


class VU:
    def __init__(self, i: int, base: str, st: Stats, ctx: dict):
        self.i, self.st, self.ctx = i, st, ctx
        self.who = ROLES[i % len(ROLES)]
        self.c = httpx.AsyncClient(base_url=base, timeout=30, limits=httpx.Limits(max_connections=4))
        self.rng = random.Random(1000 + i)
        self.n = 0

    async def login(self):
        r = await self.c.post("/api/auth/login", json={"email": f"demo.{self.who}@example.com", "password": PASSWORD})
        r.raise_for_status()
        self.c.headers["X-CSRF-Token"] = r.json()["csrf_token"]

    async def req(self, cls, method, path, **kw):
        t = time.perf_counter()
        try:
            r = await self.c.request(method, path, **kw)
            status = r.status_code
        except httpx.HTTPError as e:
            r, status = None, type(e).__name__
        self.st.rec(cls, (time.perf_counter() - t) * 1000, status)
        return r

    def key(self):
        self.n += 1
        return {"Idempotency-Key": f"load-{self.i}-{uuid.uuid4().hex[:12]}-{self.n}"}

    async def read(self):
        cls, path = self.rng.choice(READS)
        if path == "DETAIL":
            d = self.ctx["details"]
            if not d:
                return
            path = self.rng.choice(d)
        await self.req(cls, "GET", path)

    async def write(self):
        c = self.ctx
        kind = self.rng.choice(["market", "so", "pay", "pr", "opp", "read"])
        if kind == "market" and self.who in ("sales", "admin") and c["accounts"]:
            acct = self.rng.choice(c["accounts"])
            code = self.rng.choice(["FG-BW-OUD-200", "FG-BB-OLIVE-150", "FG-DETAN-60"])
            sku = c["skus"].get((acct["id"], code))
            if sku:
                oid = f"LOAD-{self.i}-{uuid.uuid4().hex[:10]}"
                await self.req("write:market-order", "POST", f"/api/ecommerce/accounts/{acct['id']}/orders", json={"orders": [{"order_id": oid, "order_date": str(date.today()), "customer_name": "Load Tester, Pune",
                                                                                                                                   "items": [{"sku": sku, "qty": "1", "unit_price": "800.00"}]}]})
        elif kind == "so" and self.who in ("disp", "admin") and c["customers"]:
            r = await self.req("write:sales-order", "POST", "/api/dispatch/orders", json={"customer_id": self.rng.choice(c["customers"]), "warehouse_id": c["wh"], "channel": "MANUAL",
                                                                                      "lines": [{"item_id": c["fg"], "qty": "2", "unit_price": "560.00"}]})
            if r is not None and r.status_code == 201:
                o = r.json()
                await self.req("write:confirm", "POST", f"/api/dispatch/orders/{o['id']}/confirm", json={"expected_version": o["version"]})
        elif kind == "pay" and self.who in ("acct", "admin") and c["invoices"]:
            inv = c["invoices"].pop() if len(c["invoices"]) > 5 else None                  # each invoice is paid by at most one VU
            if inv:
                amt = str(inv["due"])
                await self.req("write:payment", "POST", "/api/accounts/payments", headers=self.key(), json={"direction": "RECEIVED", "party_id": inv["customer_id"], "amount": amt, "payment_date": str(date.today()),
                                                                                                           "method": "UPI", "bank_account_code": "1100", "allocations": [{"invoice_id": inv["id"], "amount": amt}]})
        elif kind == "pr" and self.who in ("buyer", "admin") and c["rm"]:
            r = await self.req("write:purchase-request", "POST", "/api/procurement/purchase-requests", json={"items": [{"item_id": self.rng.choice(c["rm"]), "qty": "25"}], "notes": "load test"})
            if r is not None and r.status_code == 201:
                pr = r.json()
                await self.req("write:pr-submit", "POST", f"/api/procurement/purchase-requests/{pr['id']}/submit", json={"expected_version": pr["version"]})
        elif kind == "opp" and self.who in ("sales", "admin") and c["customers"]:
            await self.req("write:crm-deal", "POST", "/api/crm/opportunities", json={"name": f"Load deal {self.i}-{uuid.uuid4().hex[:6]}", "customer_id": self.rng.choice(c["customers"]), "expected_value": 100000})
        else:
            await self.req("write:notifications-read-all", "POST", "/api/notifications/read-all", json={})

    async def run(self, until: float, start_after: float):
        await asyncio.sleep(start_after)
        await self.login()
        while time.time() < until:
            if self.rng.random() < 0.8:
                await self.read()
            else:
                await self.write()
            await asyncio.sleep(self.rng.uniform(0.3, 1.5))                                  # think time
        await self.c.aclose()


async def sampler(st: Stats, port: int, dsn: str | None, until: float):
    try:
        import psutil
    except ImportError:
        psutil = None
    procs = []
    if psutil:
        for c in psutil.net_connections(kind="tcp"):
            if c.status == "LISTEN" and c.laddr.port == port and c.pid:
                p = psutil.Process(c.pid)
                procs = [p, *p.children(recursive=True)]
                break
        for p in procs:
            p.cpu_percent(None)
    while time.time() < until:
        await asyncio.sleep(10)
        s: dict = {"t": round(time.time() - st.t0)}
        if procs:
            alive = [p for p in procs if p.is_running()]
            s["api_rss_mb"] = round(sum(p.memory_info().rss for p in alive) / 1e6)
            s["api_cpu_pct"] = round(sum(p.cpu_percent(None) for p in alive))
        if dsn:
            try:
                import psycopg
                with psycopg.connect(dsn, autocommit=True, connect_timeout=3) as c:
                    s["db_connections"] = c.execute("select count(*) from pg_stat_activity where datname = current_database()").fetchone()[0]
            except Exception:  # noqa: BLE001
                pass
        s["requests"], s["errors"] = st.total, len(st.errors)
        st.samples.append(s)
        print(" ", s, flush=True)


def trend(xs):
    """Least-squares slope per hour of a sampled series (MB/h for memory)."""
    if len(xs) < 4:
        return None
    n = len(xs)
    mx, my = (n - 1) / 2, sum(y for _, y in xs) / n
    t = [x for x, _ in xs]
    num = sum((t[i] - sum(t) / n) * (xs[i][1] - my) for i in range(n))
    den = sum((a - sum(t) / n) ** 2 for a in t)
    return round(num / den * 3600, 1) if den else None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8101")
    ap.add_argument("--users", type=int, default=50)
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--ramp", type=float, default=60)
    ap.add_argument("--label", default="load")
    ap.add_argument("--dsn", default=os.environ.get("MINTREE_DATABASE_URL", "postgresql+psycopg://mintree:mintree@localhost:5433/mintree_demo").replace("+psycopg", ""))
    a = ap.parse_args()
    st = Stats()
    setup = httpx.AsyncClient(base_url=a.base, timeout=60)
    r = await setup.post("/api/auth/login", json={"email": "demo.admin@example.com", "password": PASSWORD})
    r.raise_for_status()
    setup.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    import psycopg
    with psycopg.connect(a.dsn, autocommit=True) as db:
        ctx = {
            "accounts": [{"id": i} for (i,) in db.execute("select id from marketplace_accounts order by id").fetchall()],
            "skus": {(acc, code): sku for acc, code, sku in db.execute("select m.account_id, i.item_code, m.platform_sku from sku_mappings m join items i on i.id = m.item_id where i.item_code in ('FG-BW-OUD-200','FG-BB-OLIVE-150','FG-DETAN-60')")},
            "customers": [i for (i,) in db.execute("select id from customers where code like 'BK-CUS-%' order by id limit 400")],
            "wh": db.execute("select id from warehouses where code = 'RM-WH'").fetchone()[0], "fg": db.execute("select id from items where item_code = 'FG-BW-OUD-200'").fetchone()[0],
            "rm": [i for (i,) in db.execute("select id from items where kind = 'RM' order by id limit 60")],
            "invoices": [{"id": i, "customer_id": c, "due": d} for i, c, d in db.execute("select id, customer_id, total - amount_paid - credited_amount from sales_invoices where status = 'OPEN' order by id desc limit 3000").fetchall()],
            "details": [],
        }
    for path in ("/api/dispatch/orders", "/api/production/orders", "/api/masters/customers", "/api/inventory/batches", "/api/procurement/purchase-orders"):
        rr = await setup.get(path, params={"page_size": 30})
        if rr.status_code == 200:
            body = rr.json()
            items = body["items"] if isinstance(body, dict) and "items" in body else body
            ctx["details"] += [f"{path}/{x['id']}" for x in (items or [])[:15] if isinstance(x, dict) and "id" in x]
    await setup.aclose()
    port = int(a.base.rsplit(":", 1)[-1].split("/")[0])
    until = time.time() + a.minutes * 60
    print(f"{a.label}: {a.users} users, {a.minutes} min, ramp {a.ramp:.0f} s, base {a.base}", flush=True)
    vus = [VU(i, a.base, st, ctx) for i in range(a.users)]
    await asyncio.gather(sampler(st, port, a.dsn, until), *[v.run(until, a.ramp * i / max(1, a.users)) for i, v in enumerate(vus)], return_exceptions=False)
    dur = time.time() - st.t0
    classes = {}
    for cls, xs in sorted(st.lat.items()):
        classes[cls] = {"n": len(xs), "p50": pct(xs, 50), "p95": pct(xs, 95), "p99": pct(xs, 99), "max": round(max(xs), 1)}
    steady = [s for s in st.samples if s["t"] > a.ramp + 30]
    rss = [(s["t"], s["api_rss_mb"]) for s in steady if "api_rss_mb" in s]
    out = {"label": a.label, "users": a.users, "minutes": a.minutes, "duration_s": round(dur), "requests": st.total, "rps": round(st.total / dur, 1), "server_errors_or_failures": len(st.errors),
           "error_rate": round(len(st.errors) / max(1, st.total), 5), "business_conflicts": st.conflicts, "over_2s": st.slow, "classes": classes,
           "error_breakdown": {f"{c}:{s}": sum(1 for x in st.errors if x == (c, s)) for c, s in set(st.errors)},
           "rss_trend_mb_per_hour": trend(rss), "rss_start_end_mb": [rss[0][1], rss[-1][1]] if rss else None, "samples": st.samples}
    budgets = {"dashboard": 300, "list": 250, "detail": 200, "search": 150}
    print(f"\n{a.label}: {st.total} requests, {out['rps']} req/s, errors {len(st.errors)} ({out['error_rate'] * 100:.3f}%), conflicts {st.conflicts}, over 2 s: {st.slow}")
    print(f"{'class':<28}{'n':>7}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}")
    for cls, v in classes.items():
        flag = "  <-- over read budget" if cls in budgets and v["p95"] > budgets[cls] else ""
        print(f"{cls:<28}{v['n']:>7}{v['p50']:>8}{v['p95']:>8}{v['p99']:>8}{v['max']:>8}{flag}")
    print("memory:", out["rss_start_end_mb"], "MB, trend", out["rss_trend_mb_per_hour"], "MB/h")
    outdir = ROOT / "tests" / "load" / "results"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / f"{a.label}-{a.users}vu-{int(a.minutes)}min.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("report:", path)


if __name__ == "__main__":
    asyncio.run(main())
