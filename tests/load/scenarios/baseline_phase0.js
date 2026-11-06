// Baseline for the Phase 0 endpoints only (no inventory yet). Establishes pre-optimisation numbers.
//   k6 run -e BASE_URL=http://127.0.0.1:8000 -e VUS=100 -e DURATION=60s scenarios/baseline_phase0.js
import { loginPool, session, pickOp, call } from "../lib/common.js";

export const options = {
  scenarios: {
    mixed: { executor: "constant-vus", vus: parseInt(__ENV.VUS || "100", 10), duration: __ENV.DURATION || "60s" },
  },
  thresholds: {
    "http_req_duration{kind:read}": ["p(95)<500"],
    "http_req_duration{kind:write}": ["p(95)<1000", "p(99)<3000"],
    system_failures: ["rate<0.005"],
    "http_req_failed{kind:read}": ["rate<0.005"],
  },
  summaryTrendStats: ["avg", "min", "med", "p(90)", "p(95)", "p(99)", "max"],
};

const MIX = { list: 40, search: 20, me: 15, audit: 5, users: 5, write: 15 };
const WORDS = ["serum", "cream", "bottle", "pump", "carton", "glycerin", "niacinamide"];

export function setup() { return loginPool(); }

export default function (data) {
  const s = session(data);
  switch (pickOp(MIX)) {
    case "list": call("GET", `/api/masters/items?page=${1 + Math.floor(Math.random() * 50)}&page_size=25`, null, s, { name: "GET /masters/items" }); break;
    case "search": call("GET", `/api/masters/items?q=${WORDS[Math.floor(Math.random() * WORDS.length)]}&page_size=25`, null, s, { name: "GET /masters/items?q" }); break;
    case "me": call("GET", "/api/auth/me", null, s, { name: "GET /auth/me" }); break;
    case "audit": call("GET", "/api/admin/audit-logs?page_size=50", null, s, { name: "GET /admin/audit-logs" }); break;
    case "users": call("GET", "/api/admin/users?page_size=50", null, s, { name: "GET /admin/users" }); break;
    case "write": call("POST", "/api/masters/brands", { code: `LB-${__VU}-${__ITER}-${Date.now() % 1000000}`, name: "load brand" }, s, { kind: "write", name: "POST /masters/brands" }); break;
  }
}
