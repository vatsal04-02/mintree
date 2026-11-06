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
export const titleCase = (s: string) => s.replace(/_/g, " ").toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());
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
