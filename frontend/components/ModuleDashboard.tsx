"use client";
/** The module landing-page dashboard, drawn in the same language as the Overview. It reads whatever the module's /dashboard endpoint returns
 *  and lays it out by shape, so every module gets the same treatment:
 *    the first four figures            -> KPI chips with icons
 *    a rate figure (win rate, release) -> the green donut card beside the hero chart
 *    problem figures above zero        -> "Needs attention" record cards (overdue, failed, breakdown, rejected, expired ...)
 *    the remaining figures             -> an "At a glance" panel of hairline-divided cells
 *    "<name>_trend" arrays             -> the hero chart (money as an orange line, counts as bars) and smaller trend cards
 *    { label: number } objects         -> a stage-mix card for the first status breakdown, coloured bar panels for the rest
 *  Whether a figure is money is decided from its name, as everywhere else in the module screens. Nothing here is invented: an empty section is omitted. */
import Link from "next/link";
import type { LucideIcon } from "lucide-react";
import { Activity, Boxes, CalendarClock, FlaskConical, IndianRupee, Megaphone, Package, Percent, ShieldCheck, Users, Wrench } from "lucide-react";
import { ReportView, isMoneyKey } from "./ModuleKit";
import { AreaChart, CountUp, InView } from "./fx";
import { Strip, type StripCard } from "./kpi";
import { CardHead, DealCard, Donut, StageMix } from "./rapport";
import { fieldLabel, fmtDay, fmtNum, fmtRupees, titleCase } from "@/lib/format";

type Point = { label: string; value: number };
const isTrend = (k: string, v: unknown): v is Point[] => k.endsWith("_trend") && Array.isArray(v) && v.length > 0 && v.every((p) => p && typeof (p as Point).value === "number");
const isBars = (v: unknown): v is Record<string, number> => !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v).length > 0 && Object.values(v).every((x) => typeof x === "number");
const isNum = (v: unknown): v is number | string => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v));
/** { group: { count: 3, value: 1200 } }: a breakdown whose rows carry several figures each (quotations by status with count and value). */
type Group = Record<string, Record<string, number | string>>;
const isGroup = (v: unknown): v is Group => !!v && typeof v === "object" && !Array.isArray(v) && Object.values(v).length > 0
  && Object.values(v).every((r) => !!r && typeof r === "object" && !Array.isArray(r) && Object.values(r).length > 0 && Object.values(r).every(isNum));
const isRate = (k: string) => /(pct|percent|_rate$|^rate$|rate_)/i.test(k) && !/(count|days)/i.test(k);

const fmt = (k: string, n: number) => (isMoneyKey(k) ? fmtRupees(n) : isRate(k) ? `${fmtNum(n, 1)}%` : fmtNum(n, Number.isInteger(n) ? 0 : 1));
/** Labels come from field names: keep acronyms upper-case, spell follow-ups, put day windows in brackets, drop a trailing "%". */
const nice = (t: string) => t.replace(/\b(skus|sku|gst|qc|rfq|capa|coa|po|so|rto|ctr)\b/gi, (m) => (m.toLowerCase() === "skus" ? "SKUs" : m.toUpperCase()))
  .replace(/\b(F|f)ollowups\b/g, "$1ollow-ups").replace(/\b(F|f)ollowup\b/g, "$1ollow-up").replace(/\b(\d+)d\b/, "($1 days)").replace(/ %$/, "").replace(/([a-z]) days$/i, "$1 (days)");
const label = (k: string) => nice(fieldLabel(k));

/** Figures that describe a problem: shown as attention cards when above zero. Awaiting/pending items are a review, not an alarm. */
const BAD_KEY = /(overdue|failed|delayed|reject|breakdown|expired|exception|unmatched|rto|lost)/i;
const REVIEW_KEY = /(awaiting|pending|quarantine|due)/i;
const ICONS: [RegExp, LucideIcon][] = [[/(rate|pct|percent)/i, Percent], [/expir/i, CalendarClock], [/(item|stock|sku|material|line)/i, Boxes], [/(revenue|value|spend|amount|outstanding|cost|charge|payable|receivable)/i, IndianRupee],
  [/(order|shipment|parcel|dispatch|deliver|transit|return|pack)/i, Package], [/(lead|customer|contact|opportunit|follow|complaint)/i, Users],
  [/(inspection|deviation|capa|batch|certificate|quality)/i, ShieldCheck], [/(project|formulation|version|trial|launch)/i, FlaskConical],
  [/(work|equipment|calibration|downtime|warranty|preventive)/i, Wrench], [/(campaign|reach|impression|click|content|social|brand|result)/i, Megaphone]];
const iconFor = (k: string) => ICONS.find(([re]) => re.test(k))?.[1] ?? Activity;

/** Workflow order for status and stage lists, so a pipeline reads start to finish instead of by size. Unknown labels keep their order after these. */
const FLOW = ["NEW", "OPEN", "CONTACTED", "PROSPECTING", "QUALIFIED", "PROPOSAL", "NEGOTIATION", "CONCEPT", "DEVELOPMENT", "TRIALS", "DRAFT", "SUBMITTED", "SENT", "REQUESTED", "PENDING", "INVESTIGATING", "ASSIGNED", "IN_PROGRESS",
  "BOOKED", "IN_TRANSIT", "ACCEPTED", "APPROVED", "RELEASE", "PASSED", "WON", "RESOLVED", "DECIDED", "DONE", "CLOSED", "COMPLETED", "DELIVERED", "REJECT", "REJECTED", "FAILED", "LOST", "CANCELLED", "OBSOLETE", "EXPIRED"];
const flowIndex = (k: string) => { const i = FLOW.indexOf(k.toUpperCase().replace(/\s+/g, "_")); return i < 0 ? FLOW.length : i; };
const sortFlow = (name: string, entries: [string, number][]) => (/(status|decision|stage)/i.test(name) && entries.some(([k]) => flowIndex(k) < FLOW.length) ? [...entries].sort(([a], [b]) => flowIndex(a) - flowIndex(b)) : entries);
/** The demo courier is a simulator ("fake"); say so rather than show the internal provider name. */
const pretty = (k: string) => (/^(fake|sim|simulated)$/i.test(k) ? "Simulated courier" : /^[B-DF-HJ-NP-TV-Z]{2,3}$/.test(k) ? k : /^[A-Z0-9_]+$/.test(k) ? titleCase(k) : k);

/** Bar colour by meaning: done green, problems orange, waiting yellow, everything else ink. */
const GOOD = /(release|pass|approv|closed|done|deliver|won|operational|resolved|paid|matched|completed|decided|received|published)/i;
const BAD = /(exception|reject|lost|fail|overdue|breakdown|expired|revoked|cancel|rto)/i;
const WARN = /(pending|review|draft|paused|investigat|quarantine|submitted|open|unmatched|partial|requested|booked|transit|assigned|in progress)/i;
const barColor = (semantic: boolean, k: string) => (!semantic ? "var(--ink-r)" : BAD.test(k) ? "var(--acc-orange)" : GOOD.test(k) && !/unmatched/i.test(k) ? "var(--acc-green)" : WARN.test(k) ? "var(--acc-yellow)" : "var(--ink-r)");

/** Stage-mix colour: meaning first (done green, problem orange, waiting yellow); neutral stages alternate ink and grey. */
const mixColor = (k: string, i: number) => { const c = barColor(true, k); return c !== "var(--ink-r)" ? c : i % 2 ? "var(--faint)" : "var(--ink-r)"; };
/** Month or date keys ("Oct 2027", "2027-10"): drawn as columns in time order rather than as a list of bars. */
const isTimeKeys = (d: Record<string, number>) => Object.keys(d).length >= 4 && Object.keys(d).every((k) => /^([A-Z][a-z]{2,8}\.? \d{4}|\d{4}-\d{2}(-\d{2})?)$/.test(k));

/** An axis maximum that splits into four clean steps; small counts round up to a multiple of four so ticks are whole numbers. */
const niceMax = (v: number, whole = false) => {
  if (v <= 0) return 4;
  if (whole && v <= 20) return Math.ceil(v / 4) * 4;
  const p = 10 ** Math.floor(Math.log10(v)); const f = v / p;
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 4 ? 4 : f <= 8 ? 8 : 10) * p;
};
function compact(k: string, n: number) {
  const p = isMoneyKey(k) ? "₹" : "";
  if (n >= 1e7) return `${p}${fmtNum(n / 1e7, 1)}Cr`;
  if (n >= 1e5) return `${p}${fmtNum(n / 1e5, 1)}L`;
  if (n >= 1e3) return `${p}${fmtNum(n / 1e3, n >= 1e4 ? 0 : 1)}k`;
  return `${p}${fmtNum(n, 0)}`;
}
const trendTitle = (name: string, n: number) => label(name.replace(/_(weekly_)?trend$/, "")) + (/_weekly_trend$/.test(name) ? ", by week" : `, last ${n} days`);

/** Bars for a count series: ink bars with the most recent non-zero bar in orange, as in the reference charts. */
function Bars({ name, points, height = 168 }: { name: string; points: Point[]; height?: number }) {
  const money = isMoneyKey(name);
  const peak = points.reduce((a, p) => (p.value > a.value ? p : a), points[0]);
  const max = niceMax(peak.value, !money);
  const ticks = [0, 0.5, 1].map((f) => f * max);
  const last = points.map((p) => p.value > 0).lastIndexOf(true);
  return (
    <div className="px-[18px] pb-[18px] pt-5">
      <div className="relative pl-10" style={{ height }} role="img" aria-label={`${trendTitle(name, points.length)}: peak ${fmt(name, peak.value)} on ${fmtDay(peak.label)}`}>
        {ticks.map((t, i) => (
          <div key={i} className="absolute left-0 right-0 flex items-center" style={{ bottom: `${(t / max) * 100}%` }} aria-hidden>
            <span className="w-9 pr-2 text-right t-support leading-none -translate-y-1/2">{compact(name, t)}</span>
            <span className="flex-1 h-px" style={{ background: i === 0 ? "var(--line-strong)" : "var(--line)" }} />
          </div>
        ))}
        <div className="absolute inset-0 left-10 flex items-end gap-[6px]">
          {points.map((p, i) => (
            <div key={p.label} className="flex-1 min-w-0 h-full flex items-end justify-center group">
              <div className="w-full max-w-[26px] rounded-t-[8px] rounded-b-[2px] bar-grow transition-opacity group-hover:opacity-70" title={`${fmtDay(p.label)}: ${fmt(name, p.value)}`}
                style={{ height: `${Math.max(p.value > 0 ? 2 : 0, (p.value / max) * 100)}%`, background: i === last ? "var(--acc-orange)" : "var(--ink-r)", opacity: i === last ? 1 : 0.78, animationDelay: `${Math.min(i * 22, 600)}ms` }} />
            </div>
          ))}
        </div>
      </div>
      <div className="flex justify-between pl-10 mt-1.5 t-support" aria-hidden>
        <span>{fmtDay(points[0].label)}</span><span>{fmtDay(points[Math.floor(points.length / 2)].label)}</span><span>{fmtDay(points[points.length - 1].label)}</span>
      </div>
      {last >= 0 && <p className="pl-10 mt-2 t-support inline-flex items-center gap-1.5" aria-hidden><span className="w-2 h-2 rounded-full" style={{ background: "var(--acc-orange)" }} />{/_weekly_trend$/.test(name) ? "Most recent week" : "Most recent day with activity"}</p>}
    </div>
  );
}

export function TrendCard({ name, points, className = "", hero = false, chartH }: { name: string; points: Point[]; className?: string; hero?: boolean; chartH?: number }) {
  const h = chartH ?? (hero ? 170 : 150);
  const total = points.reduce((a, p) => a + p.value, 0);
  const title = trendTitle(name, points.length);
  const weekly = /_weekly_trend$/.test(name);
  const money = isMoneyKey(name);
  return (
    <section className={`panel rise flex flex-col min-w-0 ${className}`} aria-label={title}>
      <CardHead title={title} />
      <div className="px-[18px] -mt-1 mb-2 flex flex-wrap items-baseline gap-x-2">
        <span className={`${hero ? "t-stage" : "text-[1.125rem] leading-6 font-medium"} tabular-nums`}><CountUp text={fmt(name, total)} /></span>
        <span className="t-support">total · {weekly ? "weekly" : "daily"} average {fmt(name, total / points.length)}</span>
      </div>
      {money && points.length > 1
        ? <AreaChart points={points} weekly={weekly} height={h} name={title} color="#c6431d" summary={false} />
        : <Bars name={name} points={points} height={h} />}
    </section>
  );
}

/** Rows that each carry several figures: the first figure gets a bar, the rest sit beside it. */
function GroupCard({ name, data }: { name: string; data: Group }) {
  const rows = sortFlow(name, Object.entries(data).map(([k, r]) => [k, Number(Object.values(r)[0])] as [string, number])).map(([k]) => [k, data[k]] as const);
  const fields = Object.keys(rows[0][1]);
  const [first, ...rest] = fields;
  const max = Math.max(...rows.map(([, r]) => Number(r[first])), 1);
  return (
    <section className="panel rise flex flex-col min-w-0" aria-label={label(name)}>
      <CardHead title={label(name)} meta={rest.length ? `${label(first)} and ${rest.map((f) => label(f).toLowerCase()).join(", ")}` : undefined} />
      <ul className="px-[18px] pb-[18px] space-y-3">
        {rows.map(([k, r], i) => (
          <li key={k}>
            <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
              <span className="truncate" title={k}>{pretty(k)}</span>
              <span className="tabular-nums shrink-0">{fmt(first, Number(r[first]))}{rest.map((f) => <span key={f} className="text-muted ml-3">{fmt(f, Number(r[f]))}</span>)}</span>
            </div>
            <div className="mt-1.5 h-2 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
              <div className="h-2 rounded-full bar-grow-x" style={{ width: `${Math.max(Number(r[first]) > 0 ? 3 : 0, (Number(r[first]) / max) * 100)}%`, background: barColor(true, k), animationDelay: `${i * 70}ms` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ColumnCard({ name, data }: { name: string; data: Record<string, number> }) {
  const entries = Object.entries(data);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  const max = Math.max(...entries.map(([, v]) => v), 1);
  const peak = entries.reduce((a, e) => (e[1] > a[1] ? e : a), entries[0]);
  const short = (k: string) => (/^\d{4}-\d{2}/.test(k) ? fmtDay(k) : k.replace(/^([A-Z][a-z]{2})[a-z]*\.? (\d{2})(\d{2})$/, "$1 ’$3"));
  return (
    <section className="panel rise flex flex-col min-w-0" aria-label={label(name)}>
      <CardHead title={label(name)} meta={`${fmt(name, total)} total · peak ${short(peak[0])}`} />
      <div className="px-[18px] pb-[18px] pt-2">
        <div className="flex items-end gap-1.5 h-[132px]" role="img" aria-label={`${label(name)}: ${entries.map(([k, v]) => `${k} ${v}`).join(", ")}`}>
          {entries.map(([k, v], i) => (
            <div key={k} className="flex-1 min-w-0 h-full flex flex-col items-center justify-end gap-1" title={`${k}: ${fmt(name, v)}`}>
              <span className="text-[0.6875rem] tabular-nums text-muted">{fmt(name, v)}</span>
              <div className="w-full max-w-[22px] rounded-t-[7px] rounded-b-[2px] bar-grow" style={{ height: `${Math.max(v > 0 ? 3 : 0, (v / max) * 82)}%`, background: k === peak[0] ? "var(--acc-orange)" : "var(--ink-r)", opacity: k === peak[0] ? 1 : 0.78, animationDelay: `${i * 40}ms` }} />
            </div>
          ))}
        </div>
        <div className="flex gap-1.5 mt-1.5 border-t border-line pt-1.5" aria-hidden>
          {entries.map(([k], i) => {
            /* month on the first line; the year under it only where it changes, so nine columns still fit without truncation */
            const [m, y] = short(k).split(" ");
            const prevY = i > 0 ? short(entries[i - 1][0]).split(" ")[1] : undefined;
            return <span key={k} className="flex-1 min-w-0 text-center text-[0.6875rem] leading-[14px] text-muted"><span className="block truncate">{m}</span><span className="block h-[14px] text-faint">{y !== prevY ? y : ""}</span></span>;
          })}
        </div>
      </div>
    </section>
  );
}

function BarCard({ name, data, drill }: { name: string; data: Record<string, number>; drill?: Drill }) {
  const semantic = /(status|decision|stage|kind)/i.test(name);
  const entries = sortFlow(name, Object.entries(data));
  const max = Math.max(...entries.map(([, v]) => v), 1);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  return (
    <section className="panel rise flex flex-col min-w-0" aria-label={label(name)}>
      <CardHead title={label(name)} meta={`${fmt(name, total)} total`} />
      <ul className="px-[18px] pb-[18px] space-y-3">
        {entries.map(([k, v], i) => (
          <li key={k}>
            {(() => { const href = drill?.(name, k); const row = (
              <div className="flex items-baseline justify-between gap-3 text-[0.8125rem]">
                <span className="truncate" title={k}>{pretty(k)}</span>
                <span className="tabular-nums shrink-0">{fmt(name, v)}</span>
              </div>); return href ? <Link href={href} className="block hover:opacity-80" aria-label={`${pretty(k)}: ${fmt(name, v)}. Show these records`}>{row}</Link> : row; })()}
            <div className="mt-1.5 h-2 rounded-full" style={{ background: "var(--chart-track)" }} aria-hidden>
              <div className="h-2 rounded-full bar-grow-x" style={{ width: `${Math.max(v > 0 ? 3 : 0, (v / max) * 100)}%`, background: barColor(semantic, k), animationDelay: `${i * 70}ms` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** Where a slice of a breakdown lives: (breakdown name, key) -> a list address, or nothing when there is no such list. */
export type Drill = (name: string, key: string) => string | undefined;

export function ModuleDashboard({ data, drill }: { data: Record<string, unknown>; drill?: Drill }) {
  const entries = Object.entries(data);
  const nums = entries.filter(([, v]) => isNum(v)).map(([k, v]) => [k, Number(v)] as [string, number]);
  const notes = entries.filter(([, v]) => typeof v === "string" && !isNum(v)) as [string, string][];
  const trends = entries.filter(([k, v]) => isTrend(k, v)) as [string, Point[]][];
  const bars = entries.filter(([k, v]) => !isTrend(k, v) && isBars(v)) as [string, Record<string, number>][];
  const groups = entries.filter(([k, v]) => !isTrend(k, v) && !isBars(v) && isGroup(v)) as [string, Group][];
  const other = Object.fromEntries(entries.filter(([k, v]) => !isTrend(k, v) && !isBars(v) && !isGroup(v) && v !== null && typeof v === "object"));

  const rate = nums.find(([k]) => isRate(k));
  const attention = nums.filter(([k, v]) => v > 0 && !isRate(k) && (BAD_KEY.test(k) || REVIEW_KEY.test(k)) && !/threshold/i.test(k));
  const chipKeys = nums.filter(([k]) => k !== rate?.[0] && !attention.some(([a]) => a === k)).slice(0, 4);
  const glance = nums.filter(([k]) => k !== rate?.[0] && !chipKeys.some(([c]) => c === k) && !attention.some(([a]) => a === k));
  const chips: StripCard[] = chipKeys.map(([k, v]) => ({ label: label(k), value: fmt(k, v), icon: iconFor(k) }));

  const [hero, ...restTrends] = trends;
  const [mix, ...restBars] = bars.filter(([k]) => /(status|stage)/i.test(k)).length ? [bars.find(([k]) => /(status|stage)/i.test(k))!, ...bars.filter(([k]) => k !== bars.find(([kk]) => /(status|stage)/i.test(kk))![0])] : [undefined, ...bars];

  const rateCard = rate && (
    <section className="panel rise flex flex-col p-4 min-w-0" aria-label={label(rate[0])}>
      <h2 className="text-[0.875rem] font-medium">{label(rate[0])}</h2>
      <div className="flex-1 grid place-items-center py-3"><Donut pct={rate[1]} label={label(rate[0]).toLowerCase().includes("rate") ? "rate" : "of total"} size={128} tone="light" /></div>
    </section>
  );

  /* Card columns chosen so no row is left with a single orphan: beside the side column 4 cards go 2 x 2, at full width they go 4 across. */
  const attnBeside = !hero && (!!rateCard || (glance.length > 0 && glance.length <= 8));
  const nA = Math.min(attention.length, 6);
  const attnCols = nA === 1 ? "sm:!grid-cols-1" : nA === 2 ? "" : nA === 4 ? (attnBeside ? "" : "lg:grid-cols-4") : "lg:grid-cols-3";
  const attentionPanel = attention.length > 0 && (
    <section className="panel rise flex flex-col min-w-0 w-full" aria-label="Needs attention">
      <CardHead title="Needs attention" meta={`${attention.length} ${attention.length === 1 ? "item" : "items"} above zero`} />
      <div className={`px-[18px] pb-[18px] grid gap-[11px] flex-1 sm:grid-cols-2 ${attnCols}`}>
        {attention.slice(0, 6).map(([k, v], i) => {
          const bad = BAD_KEY.test(k);
          return <DealCard key={k} index={i} variant="white" meta={bad ? "Act now" : "Review"} metaTone={bad ? "danger" : "warning"} valueTone={bad ? "var(--acc-orange)" : undefined} title={label(k)} value={fmt(k, v)} />;
        })}
      </div>
    </section>
  );
  /* Beside a hero chart, one or two attention figures read better as a short list in the side column than as a half-empty row of cards. */
  const attnCompact = attention.length > 0 && attention.length <= 2;
  const attentionList = attnCompact && (
    <section className="panel rise min-w-0" aria-label="Needs attention">
      <CardHead title="Needs attention" />
      <ul className="px-[18px] pb-[14px]">
        {attention.map(([k, v]) => {
          const bad = BAD_KEY.test(k);
          return (
            <li key={k} className="flex items-center justify-between gap-3 py-2.5 border-t border-line">
              <span className="inline-flex items-center gap-2 min-w-0"><span className={`pill ${bad ? "pill-danger" : "pill-warning"} shrink-0`}>{bad ? "Act now" : "Review"}</span><span className="text-[0.8125rem] truncate">{label(k)}</span></span>
              <span className="text-[1.0625rem] font-medium tabular-nums shrink-0" style={bad ? { color: "var(--acc-orange)" } : undefined}><CountUp text={fmt(k, v)} /></span>
            </li>
          );
        })}
      </ul>
    </section>
  );
  const lead = hero ? "hero" : attention.length > 2 ? "attention" : null;
  /* The figures that are not chips sit in the side column as a list when there is a lead card to sit beside and the column stays short;
     otherwise they get their own grid below. */
  const glanceSide = !!lead && glance.length > 0 && glance.length <= 8 && !(rateCard && (attnCompact || glance.length > 4));
  const side = !!rateCard || glanceSide || attnCompact;
  /* With no lead card, the rate, the short attention list and a short list of figures join the card grid instead of a half-empty row. */
  const glanceInGrid = !lead && (!!rateCard || attnCompact) && glance.length > 0 && glance.length <= 6;
  /* The hero chart grows to roughly the height of the side column so the row ends level instead of leaving a blank band under the chart. */
  const sideH = (rateCard ? 205 : 0) + (attnCompact ? 60 + 46 * attention.length : 0) + (glanceSide ? 62 + 37 * glance.length : 0)
    + 16 * Math.max(0, [rateCard, attnCompact, glanceSide].filter(Boolean).length - 1);
  const heroH = Math.max(170, Math.min(360, sideH - 125));
  const heroCard = hero && <InView className="flex w-full"><TrendCard name={hero[0]} points={hero[1]} hero chartH={heroH} className="w-full" /></InView>;
  const glanceList = (
    <section className="panel rise flex-1 min-w-0" aria-label="At a glance">
      <CardHead title="At a glance" />
      <dl className="px-[18px] pb-[14px]">
        {glance.map(([k, v]) => (
          <div key={k} className="flex items-baseline justify-between gap-3 py-2 border-t border-line">
            <dt className="t-support truncate" title={label(k)}>{label(k)}</dt>
            <dd className="text-[0.9375rem] font-medium tabular-nums shrink-0"><CountUp text={fmt(k, v)} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );
  const glanceGrid = glance.length > 0 && (
    <section className="panel rise min-w-0" aria-label="At a glance">
      <CardHead title="At a glance" />
      <dl className={`px-[18px] pb-[18px] grid grid-cols-2 md:grid-cols-3 gap-x-6 ${glance.length % 3 === 0 ? "" : "xl:grid-cols-4"}`}>
        {glance.map(([k, v]) => (
          <div key={k} className="py-3 border-t border-line min-w-0">
            <dt className="t-support truncate" title={label(k)}>{label(k)}</dt>
            <dd className="text-[1.125rem] leading-6 font-medium tabular-nums mt-0.5"><CountUp text={fmt(k, v)} /></dd>
          </div>
        ))}
      </dl>
    </section>
  );

  return (
    <div className="space-y-4 mb-6">
      {chips.length > 0 && <Strip cards={chips} className="py-1" label="Key figures" />}

      {/* Lead row: the hero chart, or failing that the attention cards, on the left; the rate donut and the remaining figures as a list on the right. */}
      {lead && (
        <div className="grid gap-4 xl:grid-cols-12 items-stretch">
          <div className={`${side ? "xl:col-span-8" : "xl:col-span-12"} flex min-w-0`}>{lead === "hero" ? heroCard : attentionPanel}</div>
          {side && (
            <div className="xl:col-span-4 flex flex-col gap-4 min-w-0">
              {rateCard}
              {attentionList}
              {glanceSide && glanceList}
            </div>
          )}
        </div>
      )}

      {lead === "hero" && !attnCompact && attentionPanel}
      {!glanceSide && !glanceInGrid && glanceGrid}

      {notes.length > 0 && notes.map(([k, v]) => <p key={k} className="panel px-5 py-3 t-label"><span className="pill pill-warning mr-2">{label(k)}</span>{v}</p>)}

      {restTrends.length > 0 && (
        <div className={`grid gap-4 ${restTrends.length > 1 ? "xl:grid-cols-2" : ""}`}>
          {restTrends.map(([k, pts], i) => <InView key={k} className={restTrends.length % 2 === 1 && i === restTrends.length - 1 && restTrends.length > 1 ? "xl:col-span-2" : ""}><TrendCard name={k} points={pts} /></InView>)}
        </div>
      )}

      {(mix || restBars.length > 0 || groups.length > 0 || (!lead && (rateCard || attnCompact))) && (
        <div className="columns-1 md:columns-2 xl:columns-3 gap-4 [&>*]:mb-4 [&>*]:break-inside-avoid">
          {!lead && rateCard}
          {!lead && attentionList}
          {glanceInGrid && glanceList}
          {mix && (
            <section className="panel rise flex flex-col min-w-0" aria-label={label(mix[0])}>
              <CardHead title={label(mix[0])} />
              <div className="px-[18px] pb-1 flex items-baseline gap-2"><span className="t-stage tabular-nums">{fmt(mix[0], Object.values(mix[1]).reduce((a, b) => a + b, 0))}</span><span className="t-support">across {Object.keys(mix[1]).length} {/stage/i.test(mix[0]) ? "stages" : "statuses"}</span></div>
              <InView><StageMix items={sortFlow(mix[0], Object.entries(mix[1])).map(([k, v], i) => ({ label: pretty(k), value: v, color: mixColor(k, i), href: drill?.(mix[0], k) }))} /></InView>
            </section>
          )}
          {restBars.map(([k, d]) => <InView key={k}>{isTimeKeys(d) ? <ColumnCard name={k} data={d} /> : <BarCard name={k} data={d} drill={drill} />}</InView>)}
          {groups.map(([k, d]) => <InView key={k}><GroupCard name={k} data={d} /></InView>)}
        </div>
      )}

      {Object.keys(other).length > 0 && <ReportView data={other} />}
    </div>
  );
}
