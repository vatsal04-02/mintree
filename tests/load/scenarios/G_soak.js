// G. Soak: 1,000 concurrent clients for 2 hours (override with -e DURATION). Watch memory, pool, queue growth in system.csv / metrics.
import { prepare, iterate, THRESHOLDS } from "../lib/workload.js";
export const options = { scenarios: { soak: { executor: "ramping-vus", startVUs: 0, gracefulRampDown: "1m", stages: [
  { duration: "3m", target: 1000 }, { duration: __ENV.DURATION || "2h", target: 1000 }] } },
  thresholds: THRESHOLDS, summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
