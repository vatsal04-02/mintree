/** Simulated plant telemetry for demonstrations.
 *
 *  The application has no sensor, SCADA, building-management, courier-SLA or weigh-scale integration. These readings exist only so the demo screens can
 *  show what a connected plant would display. Every panel or tile that reads from this file carries a visible "Simulated" (or "sim") mark; nothing here is
 *  stored, posted or used in a calculation. */

export type Shift = { name: string; from: string; to: string };
const SHIFTS: (Shift & { start: number; end: number })[] = [
  { name: "Shift Alpha", from: "06:00", to: "14:30", start: 6 * 60, end: 14 * 60 + 30 },
  { name: "Shift Beta", from: "14:30", to: "23:00", start: 14 * 60 + 30, end: 23 * 60 },
  { name: "Night shift", from: "23:00", to: "06:00", start: 23 * 60, end: 24 * 60 + 6 * 60 },
];
/** The shift pattern is a demo assumption (three shifts); the clock is real (India time). */
export function currentShift(now = new Date()): Shift {
  const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  const m = ist.getHours() * 60 + ist.getMinutes();
  return SHIFTS.find((s) => (m >= s.start && m < s.end) || (m + 24 * 60 >= s.start && m + 24 * 60 < s.end)) ?? SHIFTS[0];
}

export const CLEANROOM = {
  grade: "GMP Class 10,000",
  readings: [
    { label: "Air pressure", value: "+18 Pa" },
    { label: "Humidity", value: "44% RH" },
    { label: "Chilled WFI", value: "18.4 °C" },
  ],
};

/** One reading per running line on the Overview. */
export const LINE_READINGS = [
  { label: "Core temp", value: "68.2 °C" },
  { label: "Agitator", value: "850 rpm" },
  { label: "Speed", value: "48 bpm" },
];

/** Production screen: plant-level readings and per-vessel instrument tiles. */
export const PLANT = { oee: "91.4%", oeeDelta: "+2.1%", waterIndex: "0.08 µS/cm" };
export const VESSEL_TILES = [
  [{ label: "Agitator", value: "850 RPM" }, { label: "Core temp", value: "42.5 °C" }, { label: "Power load", value: "14.2 kWh" }],
  [{ label: "Fill speed", value: "48 bpm" }, { label: "N2 purge", value: "1.82 bar" }, { label: "Defect rate", value: "0.24%" }],
  [{ label: "Agitator", value: "620 RPM" }, { label: "Core temp", value: "38.0 °C" }, { label: "Vacuum", value: "-0.82 bar" }],
];
export const EQUIPMENT_TILES = [
  { health: "98.2% health", tiles: [{ label: "Shear rate", value: "3,200 rpm" }, { label: "Vacuum vac", value: "-0.88 bar" }, { label: "Seal pressure", value: "4.2 bar" }] },
  { health: "Active: 48 bpm", tiles: [{ label: "Nozzle dosing", value: "±0.15 ml" }, { label: "Vibration", value: "0.12 mm/s" }, { label: "Torque capper", value: "2.4 Nm" }] },
  { health: "USP compliant", tiles: [{ label: "Conductivity", value: "0.08 µS/cm" }, { label: "Total TOC", value: "< 15 ppb" }, { label: "Circ. flow", value: "1,400 L/h" }] },
];

/** Omni-channel screen: marketplace SLA, the daily cut-off, courier SLA matrix and weigh-scale discrepancies. */
export const CHANNEL_SLA: Record<string, string> = { AMAZON: "99.4%", NYKAA: "99.1%", FLIPKART: "98.2%", MYNTRA: "97.9%", SHOPIFY: "100%" };
export const DISPATCH_CUTOFF = "17:30 IST";
export const COURIER_SLA = [
  { name: "Delhivery Express Surface", sla: 99.2, transit: "1.4 days", active: 1120 },
  { name: "BlueDart Air Express", sla: 99.8, transit: "1.0 day", active: 450 },
  { name: "Shadowfax Hyperlocal", sla: 96.5, transit: "0.8 days", active: 280 },
];
export const WEIGH_SCALE = [
  { bom: "340 g", actual: "412 g", diff: "+72 g", action: "Re-weigh" },
  { bom: "180 g", actual: "125 g", diff: "-55 g", action: "Inspect" },
];

/** Purchase screen: the MRP forecast note. */
export const MRP_NOTE = "Based on festive peak demand forecasts (+38% on Nykaa and Amazon), automatic safety replenishment buffers have been recalculated.";
