// Mixed business workload for Mintree Factory OS. Weights are configurable:  -e WORKLOAD='{"list_items":10,...}'
// so they can be replaced with real Mintree operational data later.
//
// Modules that do not exist yet are NOT simulated: `accounts` and `integrations` have weight 0 and are rejected
// if enabled. `production_issue` and `dispatch` exercise the real inventory posting endpoints those modules will
// call (issue / reserve+consume) -- they are proxies, labelled as such in docs/LOAD_TEST_PLAN.md.
import { sleep } from "k6";
import http from "k6/http";
import { fail } from "k6";
import { BASE_URL, call, headers, loginPool, pickOp } from "./common.js";

export const DEFAULT_MIX = {
  // reads (~55%)
  list_items: 12, search_items: 8, balances: 10, ledger: 5, availability: 8, po_list: 4, me: 4,
  // master data + inventory + procurement + QC + production/dispatch proxies
  write_master: 3, reserve_release: 8, production_issue: 5, dispatch: 5, transfer: 4, procurement: 6,
  // not implemented yet -> must stay 0
  accounts: 0, integrations: 0,
};
const RUN = parseInt(__ENV.RUN_ID || String(Date.now() % 1000000), 10);
export const THINK_MIN = parseFloat(__ENV.THINK_MIN || "0.5");
export const THINK_MAX = parseFloat(__ENV.THINK_MAX || "2.5");
const WORDS = ["serum", "cream", "bottle", "pump", "carton", "glycerin", "niacinamide"];
let counter = 0;

export const rnd = (n) => Math.floor(Math.random() * n);
export const pick = (a) => a[rnd(a.length)];
export const uid = (op) => `${RUN}-${__VU}-${__ITER}-${op}-${counter++}`;
// Unique numeric document ids (JS-safe: RUN < 1e6, VU < 1e4, ITER < 1e5, counter < 10).
export const docId = () => RUN * 1e9 + __VU * 1e5 + (__ITER % 1e5) * 10 + (counter++ % 10);

export function think() { if (THINK_MAX > 0) sleep(THINK_MIN + Math.random() * (THINK_MAX - THINK_MIN)); }

/** setup(): log in the user pool and discover the ids the workload needs. */
export function prepare() {
  const sessions = loginPool();
  const s = sessions[0];
  const get = (p) => { const r = http.get(`${BASE_URL}${p}`, { headers: headers(s) }); if (r.status !== 200) fail(`setup ${p}: ${r.status} ${r.body}`); return r.json(); };
  const wh = get("/api/masters/warehouses?page_size=50").items;
  const rm = wh.find((w) => w.code === "RM-WH"), pm = wh.find((w) => w.code === "PM-WH");
  const bal = [];
  for (let p = 1; p <= 3; p++) {
    const page = get(`/api/inventory/balances?page=${p}&page_size=200`).items;
    bal.push(...page.filter((b) => b.item_code.startsWith("STK-")));
  }
  const supplier = get("/api/masters/suppliers?q=PSUP-&page_size=1").items[0];
  if (!rm || !pm || !bal.length || !supplier) fail("dataset missing: run backend/scripts/perf_dataset.py");
  return { sessions, rmWh: rm.id, pmWh: pm.id, stock: bal.map((b) => ({ item: b.item_id, batch: b.batch_id })), supplier: supplier.id };
}

const post = (s, path, body, opts = {}) => call("POST", path, body, s, opts);
const idem = (op) => ({ "Idempotency-Key": uid(op) });

export const OPS = {
  list_items: (s) => call("GET", `/api/masters/items?page=${1 + rnd(40)}&page_size=25`, null, s, { name: "GET /masters/items" }),
  search_items: (s) => call("GET", `/api/masters/items?q=${pick(WORDS)}&page_size=25`, null, s, { name: "GET /masters/items?q" }),
  me: (s) => call("GET", "/api/auth/me", null, s, { name: "GET /auth/me" }),
  balances: (s, d) => call("GET", `/api/inventory/balances?item_id=${pick(d.stock).item}`, null, s, { name: "GET /inventory/balances" }),
  availability: (s, d) => call("GET", `/api/inventory/availability?item_id=${pick(d.stock).item}`, null, s, { name: "GET /inventory/availability" }),
  ledger: (s, d) => call("GET", `/api/inventory/ledger?item_id=${pick(d.stock).item}&limit=50`, null, s, { name: "GET /inventory/ledger" }),
  po_list: (s) => call("GET", "/api/procurement/purchase-orders?page_size=25", null, s, { name: "GET /procurement/purchase-orders" }),
  write_master: (s) => post(s, "/api/masters/brands", { code: `LB-${uid("b")}`.slice(0, 40), name: "load brand" }, { kind: "write", name: "POST /masters/brands" }),

  reserve_release: (s, d) => {
    const it = pick(d.stock);
    const r = post(s, "/api/inventory/reservations", { item_id: it.item, warehouse_id: d.rmWh, qty: String(1 + rnd(3)), reference_type: "sales_order", reference_id: uid("so") },
      { kind: "write", name: "POST /inventory/reservations", expected: [409], extraHeaders: idem("res") });
    if (r.status === 201) {
      const ids = r.json("reservations").map((x) => x.reservation_id);
      post(s, "/api/inventory/reservations/release", { reservation_ids: ids }, { kind: "write", name: "POST /inventory/reservations/release", extraHeaders: idem("rel") });
    }
  },
  production_issue: (s, d) => {   // proxy for Production material issue
    const it = pick(d.stock);
    post(s, "/api/inventory/issues", { item_id: it.item, warehouse_id: d.rmWh, qty: String(1 + rnd(5)), source_type: "production_order", source_id: docId() },
      { kind: "write", name: "POST /inventory/issues", expected: [409], extraHeaders: idem("iss") });
  },
  dispatch: (s, d) => {           // proxy for Dispatch: allocate (reserve) then consume the reservation
    const it = pick(d.stock), qty = String(1 + rnd(3));
    const r = post(s, "/api/inventory/reservations", { item_id: it.item, warehouse_id: d.rmWh, qty, reference_type: "sales_order", reference_id: uid("so") },
      { kind: "write", name: "POST /inventory/reservations", expected: [409], extraHeaders: idem("dres") });
    if (r.status === 201) {
      const res = r.json("reservations");
      post(s, "/api/inventory/issues", { item_id: it.item, warehouse_id: d.rmWh, qty, source_type: "dispatch", source_id: docId(), reservation_ids: res.map((x) => x.reservation_id) },
        { kind: "write", name: "POST /inventory/issues (reserved)", expected: [409], extraHeaders: idem("diss") });
    }
  },
  transfer: (s, d) => {
    const it = pick(d.stock), [a, b] = Math.random() < 0.5 ? [d.rmWh, d.pmWh] : [d.pmWh, d.rmWh];
    post(s, "/api/inventory/transfers", { item_id: it.item, batch_id: it.batch, from_warehouse_id: a, to_warehouse_id: b, qty: "1", source_id: docId() },
      { kind: "write", name: "POST /inventory/transfers", expected: [409], extraHeaders: idem("trf") });
  },
  procurement: (s, d, other) => { // PO -> submit -> approve (a DIFFERENT user) -> goods receipt -> QC release
    const it = pick(d.stock);
    const po = post(s, "/api/procurement/purchase-orders", { supplier_id: d.supplier, items: [{ item_id: it.item, qty: "50", unit_price: "10" }] },
      { kind: "write", name: "POST /procurement/purchase-orders" });
    if (po.status !== 201) return;
    const p = po.json();
    const sub = post(s, `/api/procurement/purchase-orders/${p.id}/submit`, { expected_version: p.version }, { kind: "write", name: "POST /procurement/purchase-orders/{id}/submit" });
    if (sub.status !== 200) return;
    const ap = post(other, `/api/procurement/purchase-orders/${p.id}/approve`, { expected_version: sub.json("version") }, { kind: "write", name: "POST /procurement/purchase-orders/{id}/approve", expected: [409] });
    if (ap.status !== 200) return;
    const today = new Date().toISOString().slice(0, 10), exp = new Date(Date.now() + 200 * 864e5).toISOString().slice(0, 10);
    const gr = post(s, "/api/procurement/goods-receipts", { po_id: p.id, warehouse_id: d.rmWh, delivery_note_no: `DN-${uid("dn")}`.slice(0, 60),
      lines: [{ po_item_id: ap.json("items.0.id"), qty: "50", supplier_lot_no: "LOAD", mfg_date: today, expiry_date: exp }] },
      { kind: "posting", name: "POST /procurement/goods-receipts", extraHeaders: idem("gr") });
    if (gr.status !== 201) return;
    post(other, `/api/inventory/batches/${gr.json("lines.0.batch_id")}/qc-decision`, { decision: "RELEASED" },
      { kind: "write", name: "POST /inventory/batches/{id}/qc-decision", extraHeaders: idem("qc") });
  },
};

export function iterate(data) {
  const mix = __ENV.WORKLOAD ? JSON.parse(__ENV.WORKLOAD) : DEFAULT_MIX;
  if ((mix.accounts || 0) > 0 || (mix.integrations || 0) > 0) fail("accounts/integrations are not implemented yet; weights must be 0");
  const i = (__VU - 1) % data.sessions.length;
  const s = data.sessions[i], other = data.sessions[(i + 1) % data.sessions.length];
  OPS[pickOp(mix)](s, data, other);
  think();
}

export const THRESHOLDS = {
  "http_req_duration{kind:read}": ["p(95)<500"],
  "http_req_duration{kind:write}": ["p(95)<1000", "p(99)<3000"],
  "http_req_duration{kind:posting}": ["p(95)<2000", "p(99)<3000"],
  system_failures: ["rate<0.005"],
  server_errors: ["rate<0.001"],
};
