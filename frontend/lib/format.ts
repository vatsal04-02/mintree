/** The only place numbers, money, dates and sizes are turned into text. Components pass raw values here; they never call toLocaleString/toFixed/Intl themselves
 *  (an ESLint rule enforces this), so every module reads the same way. Locale is en-IN (lakh/crore grouping). */
const LOCALE = "en-IN";
const toNum = (v: unknown): number | null => {
  if (v === null || v === undefined || v === "") return null;
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : null;
};
/** Rounds first, then removes the sign of a value that rounds to zero, so -0.3 never renders as "-0" or "-₹0". */
const settle = (n: number, digits: number) => { const f = 10 ** digits; const r = Math.round(n * f) / f; return r === 0 ? 0 : r; };

const INR = (digits: number) => new Intl.NumberFormat(LOCALE, { style: "currency", currency: "INR", minimumFractionDigits: digits, maximumFractionDigits: digits });
const INR0 = INR(0), INR2 = INR(2);
const NUM = new Map<number, Intl.NumberFormat>();
const numFmt = (d: number) => { let f = NUM.get(d); if (!f) NUM.set(d, (f = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: d }))); return f; };

/** Dashboard and summary amounts: whole rupees, e.g. ₹1,42,944. Same size and position for the symbol and every digit. */
export const fmtRupees = (v?: string | number | null) => { const n = toNum(v); return n === null ? "—" : INR0.format(settle(n, 0)); };
/** Detailed records (invoices, ledgers, tables): two decimals, e.g. ₹1,42,944.04. */
export const fmtMoney = (v?: string | number | null) => { const n = toNum(v); return n === null ? "—" : INR2.format(settle(n, 2)); };
/** Counts and quantities: grouped, up to `digits` decimals with trailing zeros dropped. */
export const fmtNum = (v?: string | number | null, digits = 0) => { const n = toNum(v); return n === null ? "—" : numFmt(digits).format(settle(n, digits)); };
export const fmtPct = (v?: string | number | null, digits = 1) => { const n = toNum(v); return n === null ? "—" : `${numFmt(digits).format(settle(n, digits))}%`; };
export const fmtBytes = (n: number) => (n > 1048576 ? `${numFmt(1).format(n / 1048576)} MB` : `${numFmt(0).format(Math.max(1, Math.round(n / 1024)))} KB`);

export const fmtDate = (v?: string | null) =>
  v ? new Date(v).toLocaleDateString(LOCALE, { day: "2-digit", month: "short", year: "numeric" }) : "—";
export const fmtDateTime = (v?: string | null) =>
  v ? new Date(v).toLocaleString(LOCALE, { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "—";
/** Wall-clock time in the business zone (Asia/Kolkata), whatever the browser's own zone is: "14:05:09". */
const IST_TIME = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
export const fmtTimeIST = (v?: string | null) => (v ? IST_TIME.format(new Date(v)) : "—");
const ACRONYM = /\b(so|po|pr|grn|qc|rto|awb|sku|gst|rfq|bom|capa|coa|uom|hsn|pdf|id|ai)\b/gi;
export const titleCase = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase()).replace(ACRONYM, (m) => m.toUpperCase());
/** "5 min ago", "3 h ago", "yesterday", else the date. Pair with fmtDateTime in a title attribute for the exact time. */
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

/* ---- business dates (Asia/Kolkata): the same zone the backend uses, whatever the browser's own zone is ---- */
const IST_DAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit" });
/** Today's Indian calendar date as YYYY-MM-DD. */
export const businessToday = () => IST_DAY.format(new Date());
const parseDay = (d: string) => { const [y, m, dd] = d.split("-").map(Number); return new Date(Date.UTC(y, m - 1, dd)); };
const dayStr = (d: Date) => d.toISOString().slice(0, 10);
export const addDays = (d: string, n: number) => { const t = parseDay(d); t.setUTCDate(t.getUTCDate() + n); return dayStr(t); };
export const daysBetween = (a: string, b: string) => Math.round((parseDay(b).getTime() - parseDay(a).getTime()) / 86400000);
/** First day of the Indian financial year (1 April) that contains `d`. */
export const fyStart = (d: string) => { const [y, m] = d.split("-").map(Number); return `${m >= 4 ? y : y - 1}-04-01`; };
/** "07 Nov": a calendar date without the year, for chart axes and compact ranges. */
export const fmtDay = (v?: string | null) => (v ? parseDay(v.slice(0, 10)).toLocaleDateString(LOCALE, { day: "2-digit", month: "short", timeZone: "UTC" }) : "—");
/** Signed percentage change, e.g. "+12.4%"; null when there is nothing to compare against. */
export const fmtDelta = (now: number, before: number) => (before > 0 ? `${now >= before ? "+" : "−"}${numFmt(1).format(Math.abs(((now - before) / before) * 100))}%` : null);

/** Names that arrive in ALL CAPS ("COCOA BUTTER") are shown in title case; mixed-case names, codes and short acronyms are left alone. */
export const formatName = (s: string) =>
  /[a-z]/.test(s) ? s : s.split(/(\s+)/).map((w) => (/\d/.test(w) || /^[A-Z]{1,3}$/.test(w) || /^\s*$/.test(w) ? w : w.charAt(0) + w.slice(1).toLowerCase())).join("");

/** A readable label for a record or report key: "party_id" -> "Party ID", "as_of" -> "As of", "grand_total" -> "Grand total", "cgst" -> "CGST". Sentence case, with known abbreviations kept in capitals. */
const ABBR: Record<string, string> = { id: "ID", gst: "GST", cgst: "CGST", sgst: "SGST", igst: "IGST", hsn: "HSN", awb: "AWB", sku: "SKU", po: "PO", so: "SO", pr: "PR", qc: "QC", rfq: "RFQ", cogs: "COGS", rto: "RTO", pan: "PAN", ifsc: "IFSC", bom: "BOM", capa: "CAPA", pdf: "PDF", url: "URL", utr: "UTR", mrp: "MRP", ar: "AR", ap: "AP" };
export const fieldLabel = (k: string) => {
  const words = k.replace(/([a-z0-9])([A-Z])/g, "$1 $2").replace(/[_\s]+/g, " ").trim().toLowerCase().split(" ").filter(Boolean);
  const out = words.map((w, i) => ABBR[w] ?? (w === "pct" ? "%" : i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w));
  return out.join(" ");
};