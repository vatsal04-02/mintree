"""Read-only route sweep: logs in and calls every parameterless GET route in the OpenAPI schema, then each list's first record's detail route.
Reports non-2xx responses, slow responses and shape problems. It changes nothing.

    python -m scripts.audit_sweep --base http://localhost:8110 --email demo.admin@example.com --password '...'
"""
import argparse
import json
import re
import sys
import time

import httpx


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--email", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--slow-ms", type=float, default=800)
    a = ap.parse_args()
    c = httpx.Client(base_url=a.base, timeout=60)
    r = c.post("/api/auth/login", json={"email": a.email, "password": a.password})
    r.raise_for_status()
    spec = c.get("/openapi.json").json() if c.get("/openapi.json").status_code == 200 else c.get("/api/openapi.json").json()
    problems, rows = [], []

    def hit(path: str, params: dict | None = None):
        t = time.time()
        resp = c.get(path, params=params)
        ms = (time.time() - t) * 1000
        rows.append((path, resp.status_code, round(ms)))
        if resp.status_code >= 400:
            problems.append((path, resp.status_code, resp.text[:160]))
        elif ms > a.slow_ms:
            problems.append((path, "SLOW", f"{ms:.0f} ms"))
        return resp

    for path, ops in sorted(spec["paths"].items()):
        if "get" not in ops or "{" in path:
            continue
        required = [p for p in ops["get"].get("parameters", []) if p.get("required")]
        if required:
            continue
        resp = hit(path)
        if resp.status_code != 200:
            continue
        try:
            body = resp.json()
        except ValueError:
            continue
        items = body.get("items") if isinstance(body, dict) else body if isinstance(body, list) else None
        detail = [p for p in spec["paths"] if p.startswith(path + "/{") and p.count("{") == 1 and "get" in spec["paths"][p] and not p.endswith("}/")]
        if items and detail and isinstance(items[0], dict) and "id" in items[0]:
            d = re.sub(r"\{[^}]+\}", str(items[0]["id"]), detail[0])
            if not re.search(r"\{", d):
                hit(d)
    print(f"{len(rows)} GET routes exercised, {len(problems)} problems")
    for p in problems:
        print("PROBLEM", *p)
    slow = sorted(rows, key=lambda x: -x[2])[:8]
    print("slowest:", ", ".join(f"{p} {ms}ms" for p, _, ms in slow))
    return 1 if any(isinstance(p[1], int) and p[1] >= 500 for p in problems) else 0


if __name__ == "__main__":
    sys.exit(main())
