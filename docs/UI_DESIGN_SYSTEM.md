# UI Design System (Overview V2)

Direction: a black-and-white foundation with neutral greys, strong typography and one restrained brand accent (Mintree teal). Flat surfaces, hairline borders, structure from spacing and alignment. No gradients, no dotted canvas, no glow, no decorative status dots.

## Tokens (`frontend/app/globals.css`)

| Token | Light | Dark | Use |
|---|---|---|---|
| `--bg` | `#f7f7f5` | `#0e0e0e` | page |
| `--surface` | `#ffffff` | `#171717` | the few framed surfaces |
| `--surface-2` | `#f1f1ef` | `#1f1f1f` | row hover, control tracks |
| `--fg` | `#171717` | `#ededeb` | text |
| `--muted` | `#767676` | `#9a9a96` | secondary text (the brief's `#777777`, one step darker so it clears 4.5:1 on white) |
| `--faint` | `#999999` | `#6f6f6b` | tertiary only (chevrons, ordinals); never the sole carrier of meaning |
| `--line` / `--line-strong` | `#e8e8e5` / `#dadad6` | `#2a2a2a` / `#3a3a38` | hairlines / stronger borders and the "planned" series |
| `--sidebar` / `--sidebar-active` | `#111111` / `#292929` | `#090909` / `#292929` | navigation |
| `--accent` | `#01877a` | `#2bb3a3` | one chart series and nothing else; never a fill for whole components |
| `--success/warning/danger/info` | status colours, used **only** where operationally meaningful (overdue, failed, expired) |

Radii 6 (controls) and 10 (surfaces). Transitions 140 ms, colour only; nothing moves on hover. `prefers-reduced-motion` is honoured.

## Type scale

page title 26/32 600 · section 15/22 600 · KPI value 24/30 500 (21/28 on phones) · label 12.5/18 · supporting 12/18 · table/list value 13/20. Digits are always tabular and lining (set on `body`). Currency is whole rupees in Indian grouping on dashboards (`fmtRupees`), two decimals in records (`fmtMoney`); the symbol and every digit share one size. ESLint blocks ad-hoc number formatting outside `lib/format.ts`.

## Primitives (`frontend/components/blocks.tsx`)

| Primitive | Purpose | Reuse notes |
|---|---|---|
| `MetricStrip` | one surface, up to four indicators with hairline separators; label / value / note rows share baselines | 4 across from `xl`, 2x2 below, labels wrap on phones |
| `Panel` / `Block` | a framed surface (use sparingly) / an unframed heading with a hairline | default to `Block`; frame only the focal content |
| `GroupedBars` + `Legend` | SVG chart with ticks, grid, tooltip, keyboard focus and a screen-reader table; fills its panel; draws nothing for periods without data | no chart library; pass real buckets, `null` means no data |
| `ExceptionList` | prioritised "needs attention" rows ("Act now", "Review"), each a direct link | callers pass only real non-zero conditions |
| `Pipeline` | a horizontal stage sequence with counts and one honest flag per stage | scrolls sideways below `xl` |
| `TransactionTable` | reference, activity, related record, date, status | row hover, horizontal scroll when narrow |
| `ActivityTimeline` | compact feed with hairline separators | |
| `Segmented`, `EmptyState`, `Skeleton` | period control, honest empty states, loading placeholders | |

Components take already-formatted text and raw series values; they never fetch or format. Data comes from `lib/summary.ts` (`useSummary`: `/analytics/management-summary`, `usePeriod`: `/analytics/overview-period`), so a metric has one source and one value everywhere. Dates use the Asia/Kolkata business day (`businessToday`, `addDays`, `fyStart` in `lib/format.ts`).

## Rules for new dashboards

1. One metric strip, at most four indicators, none repeated further down.
2. The first framed element is the focal analysis; everything else is an unframed `Block`.
3. Zero and "no data" differ: show an empty state with the reason, never a zero-filled chart.
4. Colour means something (overdue, failed, expired) or it is grey. The accent is for one series.
5. Every interactive row is a real link with a 140 ms hover and a visible focus ring.
