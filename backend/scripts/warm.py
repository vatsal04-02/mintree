"""Warm-up and read-latency probe against a RUNNING API (docs/DEMO_RUNBOOK.md, `make warm` / `make perf`).

    python -m scripts.warm --base http://localhost:8101 --email demo.admin@example.com --password '...'            # prime caches, pools, ORM metadata (2 passes)
    python -m scripts.warm ... --passes 15 --report                                                              # also print p50/p95/p99 per class against the budgets

Calls every parameterless GET route as the given user, each list with page_size=50, a sort, and a search; each list's first record's detail route;
and the command-palette searches. Read-only. The first pass is "cold", the rest "warm". Exit code 1 if a warm budget is missed (with --report)."""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time

import httpx

SKIP = ("/events", "/demo/", "/health", "/ready", "/metrics", "/docs", "/openapi", "/auth/", "/documents/", "/pdf", "/export")
BUDGET_MS = {"dashboard": 300, "list": 250, "detail": 200, "search": 150}                  # warm p95
COLD_MS = {"dashboard": 800}
PALETTE = [("/api/masters/items", {"q": "oud", "page_size": 8}), ("/api/masters/suppliers", {"q": "chem", "page_size": 8}), ("/api/masters/customers", {"q": "beauty", "page_size": 8}),
           ("/api/masters/items", {"q": "BK-RM", "page_size": 8})]
DASH = re.compile(r"(dashboard|/hub|omnihub|growth|management-summary|overview-|-board|customers-board|gst-summary|annotations)$")


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))] if xs else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=60)
    r = c.post("/api/auth/login", json={"email": a.email, "password": a.password})
    r.raise_for_status()
    spec = c.get("/api/openapi.json").json()
    gets = sorted(p for p, ops in spec["paths"].items() if "get" in ops and "{" not in p and not any(s in p for s in SKIP) and not [q for q in ops["get"].get("parameters", []) if q.get("required")])
    jobs: list[tuple[str, str, dict]] = []                                   # (class, path, params)
    errors: list[tuple[str, int | str]] = []
    discovered: list[tuple[str, str, dict]] = []
    for p in gets:
        try:
            r0 = c.get(p, params={"page_size": 50})
        except httpx.HTTPError as e:
            print(f"TRANSPORT ERROR {p}: {type(e).__name__}", file=sys.stderr)
            errors.append((p, type(e).__name__))
            continue
        if r0.status_code != 200:
            print(f"skip {p}: {r0.status_code}", file=sys.stderr)
            continue
        try:
            body = r0.json()
        except ValueError:
            continue
        items = body.get("items") if isinstance(body, dict) else body if isinstance(body, list) else None
        if DASH.search(p):
            jobs.append(("dashboard", p, {}))
        elif isinstance(items, list):
            jobs += [("list", p, {"page_size": 50}), ("list", p, {"page_size": 50, "page": 2}), ("search", p, {"page_size": 20, "q": "a"})]
            detail = [d for d in spec["paths"] if d.startswith(p + "/{") and d.count("{") == 1 and "get" in spec["paths"][d]]
            if items and detail and isinstance(items[0], dict) and "id" in items[0] and not any(x in detail[0] for x in SKIP):
                discovered.append(("detail", re.sub(r"\{[^}]+\}", str(items[0]["id"]), detail[0]), {}))
        else:
            jobs.append(("dashboard" if "analytics" in p or "report" in p else "list", p, {}))
    jobs += discovered + [("search", p, q) for p, q in PALETTE]
    samples: dict[tuple[str, str, str], list[float]] = {}
    for n in range(a.passes):
        for cls, p, params in jobs:
            t = time.perf_counter()
            try:
                resp = c.get(p, params=params)
            except httpx.HTTPError as e:
                errors.append((p, type(e).__name__))
                continue
            ms = (time.perf_counter() - t) * 1000
            if resp.status_code >= 400:
                errors.append((p, resp.status_code))
                continue
            samples.setdefault((cls, p, json.dumps(params, sort_keys=True)), []).append(ms if n else -ms)       # negative = cold pass (marked)
    cold = {k: [-x for x in v if x < 0] for k, v in samples.items()}
    warm = {k: [x for x in v if x >= 0] for k, v in samples.items()}
    print(f"warm-up done: {len(jobs)} requests per pass x {a.passes}, {len(errors)} errors")
    for e in sorted(set(errors))[:10]:
        print("  ERROR", *e)
    if not a.report:
        return 0 if not errors else 1
    ok = True
    out = {"classes": {}, "slowest": []}
    print(f"\n{'class':<10}{'n':>6}{'p50':>8}{'p95':>8}{'p99':>8}{'max':>8}  budget(p95)  cold-p95")
    for cls in ("dashboard", "list", "detail", "search"):
        w = [x for k, xs in warm.items() if k[0] == cls for x in xs]
        cd = [x for k, xs in cold.items() if k[0] == cls for x in xs]
        if not w:
            continue
        p95 = pct(w, 95)
        flag = "" if p95 <= BUDGET_MS[cls] else "  <-- OVER BUDGET"
        ok &= p95 <= BUDGET_MS[cls]
        out["classes"][cls] = {"n": len(w), "p50": round(pct(w, 50)), "p95": round(p95), "p99": round(pct(w, 99)), "max": round(max(w)), "cold_p95": round(pct(cd, 95)) if cd else None}
        print(f"{cls:<10}{len(w):>6}{pct(w, 50):>8.0f}{p95:>8.0f}{pct(w, 99):>8.0f}{max(w):>8.0f}  {BUDGET_MS[cls]:>6} ms   {pct(cd, 95):>6.0f}{flag}")
    rows = sorted(((statistics.median(v), pct(v, 95), k) for k, v in warm.items() if v), reverse=True)[:15]
    print("\nslowest routes (warm median / p95 ms):")
    for med, p95, (cls, p, prm) in rows:
        print(f"  {med:7.0f} {p95:7.0f}  {cls:<9} {p} {prm if prm != '{}' else ''}")
        out["slowest"].append({"route": p, "class": cls, "params": json.loads(prm), "median": round(med), "p95": round(p95)})
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1)
    return 0 if ok and not errors else 1


if __name__ == "__main__":
    sys.exit(main())
