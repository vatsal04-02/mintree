// E. Hot-stock contention: concurrent reservations / issues / releases against ONE limited balance and batch.
// Create the dedicated item first:  python -m scripts.perf_dataset --hot-qty 1000   (prints HOT_* ids) and pass them with -e.
// Expected outcome: successes consume at most HOT_QTY units (the rest are 409 insufficient_stock = business conflicts);
// zero 5xx; the integrity checker must pass (no negative or over-reserved stock).
import { check, fail } from "k6";
import { call, loginPool, session, businessConflicts } from "../lib/common.js";
import { THRESHOLDS, uid, docId } from "../lib/workload.js";

const H = { item: parseInt(__ENV.HOT_ITEM_ID), wh: parseInt(__ENV.HOT_WAREHOUSE_ID), batch: parseInt(__ENV.HOT_BATCH_ID) };
export const options = { scenarios: { hot: { executor: "constant-vus", vus: parseInt(__ENV.VUS || "200", 10), duration: __ENV.DURATION || "60s" } },
  thresholds: { server_errors: ["rate<0.001"], system_failures: ["rate<0.005"], "http_req_duration{kind:write}": ["p(95)<1000", "p(99)<3000"] },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export function setup() { if (!H.item) fail("pass -e HOT_ITEM_ID -e HOT_WAREHOUSE_ID -e HOT_BATCH_ID"); return loginPool(); }
export default function (data) {
  const s = session(data), h = (op) => ({ "Idempotency-Key": uid(op) });
  const r = call("POST", "/api/inventory/reservations", { item_id: H.item, warehouse_id: H.wh, qty: "1", reference_type: "hot", reference_id: uid("h") }, s,
    { kind: "write", name: "POST /inventory/reservations (hot)", expected: [409], extraHeaders: h("res") });
  if (r.status !== 201) return;
  const ids = r.json("reservations").map((x) => x.reservation_id), roll = Math.random();
  if (roll < 0.5)      call("POST", "/api/inventory/issues", { item_id: H.item, warehouse_id: H.wh, qty: "1", source_type: "hot", source_id: docId(), reservation_ids: ids }, s,
                            { kind: "write", name: "POST /inventory/issues (hot, reserved)", expected: [409], extraHeaders: h("iss") });
  else if (roll < 0.8) call("POST", "/api/inventory/reservations/release", { reservation_ids: ids }, s, { kind: "write", name: "POST /inventory/reservations/release (hot)", extraHeaders: h("rel") });
  // else: keep the reservation (consumes available stock until the run ends)
}
