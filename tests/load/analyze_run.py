#!/usr/bin/env python3
"""Per-interval timeline of a load run, rebuilt from the Prometheus snapshots the harness saved.

    python3 tests/load/analyze_run.py B-progressive-p1 [--csv]

For each scrape interval: req/s, latency percentiles (histogram bucket deltas, summed over routes), 5xx and 503-shed counts,
admission queue (waiting / in-flight), pool checkouts, DB tx p95, outbox backlog, process CPU. This is what reveals *where*
a staircase test broke or whether a soak is drifting -- the k6 summary alone averages everything.
"""
import json
import re
import sys
from pathlib import Path

RES = Path(__file__).resolve().parent / "results"


def parse(path):
    """-> {metric_name: {labels_tuple: value}}"""
    out = {}
    for line in open(path):
        if line.startswith("#") or not line.strip():
            continue
        m = re.match(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})? ([0-9.eE+\-]+|NaN|Inf|\+Inf)$", line.strip())
        if not m:
            continue
        name, labels, val = m.groups()
        lab = tuple(sorted(re.findall(r'(\w+)="([^"]*)"', labels or "")))
        try:
            out.setdefault(name, {})[lab] = float(val)
        except ValueError:
            pass
    return out


def total(d, name, **match):
    return sum(v for lab, v in d.get(name, {}).items() if all(dict(lab).get(k) == x for k, x in match.items()))


def hist_q(prev, cur, name, q):
    """Quantile (bucket upper edge) of the observations added between two snapshots."""
    def buckets(d):
        b = {}
        for lab, v in d.get(name + "_bucket", {}).items():
            le = dict(lab)["le"]
            b[le] = b.get(le, 0) + v
        return b
    a, c = buckets(prev), buckets(cur)
    delta = {le: c[le] - a.get(le, 0) for le in c}
    n = delta.get("+Inf", 0)
    if n <= 0:
        return None
    for le in sorted((k for k in delta if k != "+Inf"), key=float):
        if delta[le] / n >= q:
            return float(le)
    return float("inf")


def main():
    label = sys.argv[1]
    d = RES / label
    snaps = sorted(d.glob("metrics-t*.prom"))
    if not snaps:
        sys.exit("no periodic snapshots")
    meta = json.loads((d / "meta.json").read_text())
    interval = 15.0
    sysrows = []
    if (d / "system.csv").exists():
        import csv
        sysrows = list(csv.DictReader(open(d / "system.csv")))
        if len(sysrows) > 1:
            interval = float(sysrows[1]["t"]) - float(sysrows[0]["t"])
    prev = parse(d / "metrics-start.prom")
    rows = []
    for i, p in enumerate(snaps):
        cur = parse(p)
        reqs = total(cur, "mintree_http_requests_total") - total(prev, "mintree_http_requests_total")
        e5 = sum(v for lab, v in cur.get("mintree_http_requests_total", {}).items() if dict(lab).get("status", "")[:1] == "5") - \
             sum(v for lab, v in prev.get("mintree_http_requests_total", {}).items() if dict(lab).get("status", "")[:1] == "5")
        row = {"t_s": round((i + 1) * interval), "req_s": round(reqs / interval, 1),
               "p50_ms": (hist_q(prev, cur, "mintree_http_request_duration_seconds", 0.5) or 0) * 1000,
               "p95_ms": (hist_q(prev, cur, "mintree_http_request_duration_seconds", 0.95) or 0) * 1000,
               "p99_ms": (hist_q(prev, cur, "mintree_http_request_duration_seconds", 0.99) or 0) * 1000,
               "5xx": int(e5), "shed": int(total(cur, "mintree_admission_shed_total") - total(prev, "mintree_admission_shed_total")),
               "waiting": int(total(cur, "mintree_admission_waiting")), "in_flight": int(total(cur, "mintree_http_in_flight")),
               "pool_out": int(total(cur, "mintree_db_pool_checked_out")), "pool_timeouts": int(total(cur, "mintree_db_pool_timeouts_total")),
               "tx_p95_ms": (hist_q(prev, cur, "mintree_db_transaction_duration_seconds", 0.95) or 0) * 1000,
               "adm_wait_p95_ms": (hist_q(prev, cur, "mintree_admission_wait_seconds", 0.95) or 0) * 1000,
               "retries": int(total(cur, "mintree_tx_retries_total")), "deadlocks": int(total(cur, "mintree_pg_deadlocks_total")),
               "outbox": int(total(cur, "mintree_outbox_unpublished")), "pg_active": int(total(cur, "mintree_pg_connections_active"))}
        if i < len(sysrows):
            row["cpu_pct"] = float(sysrows[i]["cpu_total_pct"]); row["api_rss_mb"] = float(sysrows[i]["api_rss_mb"]); row["worker_rss_mb"] = float(sysrows[i].get("worker_rss_mb", 0) or 0)
        rows.append(row)
        prev = cur
    cols = list(rows[0].keys())
    if "--csv" in sys.argv:
        print(",".join(cols)); [print(",".join(str(r.get(c, "")) for c in cols)) for r in rows]
        return
    print(f"{label}  commit={meta['commit'][:10]}  workers={meta['api']['workers']}  interval={interval:.0f}s")
    print(" ".join(f"{c:>9}" for c in cols))
    for r in rows:
        print(" ".join(f"{r.get(c, ''):>9.1f}" if isinstance(r.get(c), float) else f"{str(r.get(c, '')):>9}" for c in cols))


if __name__ == "__main__":
    main()
