// C. Target: 1,000 concurrent active business-operation clients for 30 minutes (override with -e DURATION).
import { prepare, iterate, THRESHOLDS } from "../lib/workload.js";
const TARGET = parseInt(__ENV.TARGET || "1000", 10);   // concurrent clients (spec target: 1000)
export const options = { scenarios: { target: { executor: "ramping-vus", startVUs: 0, gracefulRampDown: "30s", stages: [
  { duration: __ENV.RAMP_S || "2m", target: TARGET }, { duration: __ENV.DURATION || "30m", target: TARGET }] } },
  thresholds: THRESHOLDS, summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
