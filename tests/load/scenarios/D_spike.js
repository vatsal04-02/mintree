// D. Spike: 100 -> 1,000 concurrent clients in 30 s, then hold 5 minutes.
import { prepare, iterate, THRESHOLDS } from "../lib/workload.js";
export const options = { scenarios: { spike: { executor: "ramping-vus", startVUs: 100, gracefulRampDown: "30s", stages: [
  { duration: __ENV.BASE_S || "2m", target: 100 }, { duration: "30s", target: 1000 }, { duration: __ENV.HOLD || "5m", target: 1000 }] } },
  thresholds: THRESHOLDS, summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
