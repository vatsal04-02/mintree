export const fmtDate = (v?: string | null) =>
  v ? new Date(v).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";
export const fmtDateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
export const fmtNum = (v?: string | number | null, digits = 0) =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-IN", { maximumFractionDigits: digits });
export const fmtMoney = (v?: string | number | null) =>
  v === null || v === undefined || v === "" ? "—" : Number(v).toLocaleString("en-IN", { style: "currency", currency: "INR" });
export const titleCase = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
/** "5 min ago", "3 h ago", "Yesterday", else the date. Pair with fmtDateTime in a title attribute for the exact time. */
export const fmtAgo = (v?: string | null, now = Date.now()) => {
  if (!v) return "—";
  const m = Math.floor((now - new Date(v).getTime()) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} h ago`;
  if (h < 48) return "yesterday";
  return fmtDate(v);
};
