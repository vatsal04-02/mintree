// One traffic class per run, at ONE constant arrival rate (open-loop): the achieved rate, latency percentiles and failures are measured separately for each class.
//   k6 run -e CLASS=A -e RATE=1000 -e DURATION=20s -e BASE_URL=... scenarios/classes.js
// A cacheable reference reads · B uncached DB reads · C dashboards · D normal business writes · E hot-stock reservations · F production/quality-style postings (proxy: stock issues)
// G financial postings (manual journals). H (background jobs) is measured by worker_recovery.py, not here.
import http from "k6/http";
import { check, fail } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://127.0.0.1:8000";
const CLASS = __ENV.CLASS || "A";
const RATE = parseInt(__ENV.RATE || "500", 10);
const PASSWORD = __ENV.DEMO_PASSWORD || "Mintree-Demo#2026";
const USER = __ENV.PERF_USER || "demo.admin@example.com";
const SESSIONS = parseInt(__ENV.SESSIONS || "24", 10);
const unexpected = new Counter("unexpected_status");

export const options = {
  scenarios: { load: { executor: "constant-arrival-rate", rate: RATE, timeUnit: "1s", duration: __ENV.DURATION || "20s", preAllocatedVUs: Math.min(RATE, 400), maxVUs: parseInt(__ENV.MAX_VUS || "2500", 10) } },
  summaryTrendStats: ["avg", "p(50)", "p(90)", "p(95)", "p(99)", "max"],
  discardResponseBodies: false,
};

export function setup() {
  const sessions = [];
  for (let i = 0; i < SESSIONS; i++) {
    const r = http.post(`${BASE}/api/auth/login`, JSON.stringify({ email: USER, password: PASSWORD }), { headers: { "Content-Type": "application/json" } });
    if (r.status !== 200) fail(`login ${r.status} ${r.body}`);
    sessions.push({ cookie: r.cookies["mintree_session"][0].value, csrf: r.json("csrf_token") });
  }
  const h = (s) => ({ Cookie: `mintree_session=${s.cookie}`, "X-CSRF-Token": s.csrf, "Content-Type": "application/json" });
  const get = (p) => http.get(`${BASE}${p}`, { headers: h(sessions[0]) }).json();
  const items = get("/api/masters/items?page_size=100").items;
  const fg = items.filter((i) => i.kind === "FG").map((i) => i.id);
  const wh = get("/api/masters/warehouses?page_size=50").items.find((w) => w.code === "RM-WH");
  return { sessions, fg, itemIds: items.map((i) => i.id), wh: wh && wh.id };
}

const rnd = (n) => Math.floor(Math.random() * n);
const pick = (a) => a[rnd(a.length)];
const today = () => new Date().toISOString().slice(0, 10);
let seq = 0;
const key = () => `perf-${CLASS}-${__VU}-${__ITER}-${seq++}-${Date.now()}`;

const OPS = {
  A: (d, H) => {
    const r = rnd(4);
    const p = r === 0 ? `/api/masters/items?page=${1 + rnd(2)}&page_size=25` : r === 1 ? "/api/masters/units-of-measure?page_size=50" : r === 2 ? "/api/masters/warehouses?page_size=50" : "/api/masters/customers?page_size=25";
    return http.get(`${BASE}${p}`, { headers: H, tags: { name: "A " + p.split("?")[0] } });
  },
  B: (d, H) => {
    const r = rnd(5), it = pick(d.fg);
    const p = r === 0 ? `/api/inventory/balances?item_id=${it}` : r === 1 ? `/api/inventory/ledger?item_id=${it}&limit=50` : r === 2 ? `/api/dispatch/orders?page=${1 + rnd(5)}&page_size=25`
      : r === 3 ? "/api/procurement/purchase-orders?page_size=25" : `/api/inventory/availability?item_id=${it}`;
    return http.get(`${BASE}${p}`, { headers: H, tags: { name: "B " + p.split("?")[0] } });
  },
  C: (d, H) => {
    const e = today(), s = new Date(Date.now() - 29 * 864e5).toISOString().slice(0, 10);
    const p = rnd(2) === 0 ? "/api/analytics/management-summary" : `/api/analytics/overview-period?start=${s}&end=${e}`;
    return http.get(`${BASE}${p}`, { headers: H, tags: { name: "C " + p.split("?")[0] } });
  },
  D: (d, H) => http.post(`${BASE}/api/crm/leads`, JSON.stringify({ name: `Load lead ${__VU}-${__ITER}-${seq++}`, company: "Load test co", email: `load${__VU}.${__ITER}.${seq}@example.com` }), { headers: H, tags: { name: "D POST /crm/leads" } }),
  E: (d, H) => {
    const it = pick(d.fg);
    const r = http.post(`${BASE}/api/inventory/reservations`, JSON.stringify({ item_id: it, warehouse_id: d.wh, qty: "1", reference_type: "sales_order", reference_id: key() }), { headers: { ...H, "Idempotency-Key": key() }, tags: { name: "E POST /inventory/reservations" } });
    if (r.status === 201) http.post(`${BASE}/api/inventory/reservations/release`, JSON.stringify({ reservation_ids: r.json("reservations").map((x) => x.reservation_id) }), { headers: { ...H, "Idempotency-Key": key() }, tags: { name: "E POST /inventory/reservations/release" } });
    return r;
  },
  F: (d, H) => http.post(`${BASE}/api/inventory/issues`, JSON.stringify({ item_id: pick(d.fg), warehouse_id: d.wh, qty: "1", source_type: "production_order", source_id: Date.now() * 1000 + rnd(1000) }), { headers: { ...H, "Idempotency-Key": key() }, tags: { name: "F POST /inventory/issues" } }),
  G: (d, H) => http.post(`${BASE}/api/accounts/journals`, JSON.stringify({ entry_date: today(), description: `load journal ${seq++}`, lines: [{ account_code: "5400", debit: "1.00" }, { account_code: "1100", credit: "1.00" }] }), { headers: { ...H, "Idempotency-Key": key() }, tags: { name: "G POST /accounts/journals" } }),
};

export default function (d) {
  const s = d.sessions[(__VU - 1) % d.sessions.length];
  const H = { Cookie: `mintree_session=${s.cookie}`, "X-CSRF-Token": s.csrf, "Content-Type": "application/json" };
  const res = OPS[CLASS](d, H);
  const okStatus = res.status >= 200 && res.status < 300 || (CLASS === "E" || CLASS === "F") && res.status === 409;
  if (!okStatus) unexpected.add(1);
  check(res, { ok: () => okStatus });
}
