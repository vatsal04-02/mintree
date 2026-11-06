// F. Stress: find the capacity ceiling. Steps 1,000 -> 1,250 -> 1,500 (then 2,000 if STEPS=4). Thresholds are NOT enforced
// (we expect to cross them); the result is the degradation pattern: where latency, 503 shedding and failures begin.
import { prepare, iterate } from "../lib/workload.js";
const hold = __ENV.STAGE_S || "5m";
const levels = (__ENV.LEVELS || "1000,1250,1500").split(",").map(Number);
const stages = []; levels.forEach((t) => { stages.push({ duration: "1m", target: t }); stages.push({ duration: hold, target: t }); });
export const options = { scenarios: { stress: { executor: "ramping-vus", startVUs: 0, gracefulRampDown: "30s", stages } },
  summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
