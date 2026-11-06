// Shared k6 helpers for the Mintree Factory OS load suite.
import http from "k6/http";
import { check, fail } from "k6";
import { Counter, Rate, Trend } from "k6/metrics";

export const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
export const POOL_USERS = parseInt(__ENV.POOL_USERS || "200", 10); // distinct logged-in accounts shared by VUs
export const PASSWORD = __ENV.PERF_PASSWORD || "Perf-Load-Passw0rd!";

// Business outcomes are measured separately from system failures (see LOAD_TEST_PLAN.md §Metrics).
export const businessConflicts = new Counter("business_conflicts");   // 409/422 that the scenario *expects*
export const systemFailures = new Rate("system_failures");           // 5xx, 429, 401, timeouts, unexpected status
export const dur = {
  read: new Trend("op_read_ms", true),
  write: new Trend("op_write_ms", true),
  posting: new Trend("op_posting_ms", true),
};

/** Workload mix: WORKLOAD='{"list":50,"search":20,...}' overrides the defaults a scenario supplies. */
export function pickOp(defaults) {
  const mix = __ENV.WORKLOAD ? JSON.parse(__ENV.WORKLOAD) : defaults;
  const total = Object.values(mix).reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (const [op, w] of Object.entries(mix)) { if ((r -= w) < 0) return op; }
  return Object.keys(mix)[0];
}

/** Log in POOL_USERS accounts once (setup). Returns [{cookie, csrf}]. */
export function loginPool() {
  const sessions = [];
  for (let i = 0; i < POOL_USERS; i++) {
    const r = http.post(`${BASE_URL}/api/auth/login`,
      JSON.stringify({ email: `perf${String(i).padStart(4, "0")}@example.com`, password: PASSWORD }),
      { headers: { "Content-Type": "application/json" }, tags: { name: "setup_login" } });
    if (r.status !== 200) fail(`login failed for user ${i}: ${r.status} ${r.body}`);
    const cookie = r.cookies["mintree_session"][0].value;
    sessions.push({ cookie, csrf: r.json("csrf_token") });
  }
  return sessions;
}

export function session(data) { return data[(__VU - 1) % data.length]; }

export function headers(s, extra = {}) {
  return Object.assign({ Cookie: `mintree_session=${s.cookie}`, "X-CSRF-Token": s.csrf,
                         "Content-Type": "application/json" }, extra);
}

/**
 * Issue a request and classify the outcome.
 * kind: "read" | "write" | "posting"; name: stable URL tag (avoids high-cardinality URL metrics);
 * expected: extra status codes that are legitimate business outcomes for this scenario.
 */
export function call(method, path, body, s, { kind = "read", name, expected = [], extraHeaders = {} } = {}) {
  const url = `${BASE_URL}${path}`;
  const params = { headers: headers(s, extraHeaders), tags: { name: name || path.split("?")[0], kind } };
  const res = http.request(method, url, body === null ? null : JSON.stringify(body), params);
  dur[kind].add(res.timings.duration);
  const ok2xx = res.status >= 200 && res.status < 300;
  const business = expected.includes(res.status);
  if (business) businessConflicts.add(1);
  systemFailures.add(!(ok2xx || business));
  check(res, { "status is expected": () => ok2xx || business });
  return res;
}
