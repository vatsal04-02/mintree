// B. Progressive: ramp through 250, 500, 750 and 1,000 concurrent clients. STAGE_S = hold per step (spec: set to taste; default 5m).
import { prepare, iterate, THRESHOLDS } from "../lib/workload.js";
const hold = __ENV.STAGE_S || "5m", ramp = __ENV.RAMP_S || "1m";
export const options = { scenarios: { progressive: { executor: "ramping-vus", startVUs: 0, gracefulRampDown: "30s", stages: [
  { duration: ramp, target: 250 }, { duration: hold, target: 250 }, { duration: ramp, target: 500 }, { duration: hold, target: 500 },
  { duration: ramp, target: 750 }, { duration: hold, target: 750 }, { duration: ramp, target: 1000 }, { duration: hold, target: 1000 }] } },
  thresholds: THRESHOLDS, summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
