// A. Smoke: 50-100 VUs for 10 minutes (defaults per spec; override with -e VUS / -e DURATION).
import { prepare, iterate, THRESHOLDS } from "../lib/workload.js";
export const options = { scenarios: { smoke: { executor: "constant-vus", vus: parseInt(__ENV.VUS || "100", 10), duration: __ENV.DURATION || "10m" } },
  thresholds: THRESHOLDS, summaryTrendStats: ["avg", "med", "p(90)", "p(95)", "p(99)", "max"] };
export const setup = prepare;
export default iterate;
