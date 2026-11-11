# Mintree Botanical Precision design system

The visual language of the Mintree Factory OS content area. It was supplied as two specifications ("Botanical Precision ERP" and "Mintree Beauty ERP — Global
Design System & Component Specification", v2.4.0) and is implemented in `frontend/app/globals.css` (the block headed "Mintree Botanical Precision design
system") and the shared components listed at the end. The sidebar keeps its own deep-teal design and is not governed by this document.

## Principles

- Scientific purity and legibility: slate-50 canvas, white cards, high-contrast text, generous but dense layouts for factory and office monitors.
- Botanical emerald anchors actions, live states and progress.
- Colour always carries meaning: emerald = success / optimal, amber = in progress / review, rose = critical, cobalt = information / telemetry,
  violet = quarantine. Marketplace accents: Amazon `#F59E0B`, Nykaa `#EC4899`, Flipkart `#3B82F6`, Myntra `#D946EF`, D2C `#059669`.

## Tokens

| Role | Value |
| --- | --- |
| Canvas | `#F8FAFC` (slate-50) |
| Card | `#FFFFFF`, border `#E2E8F0` at 80%, radius 12px, shadow `0 1px 3px rgb(15 23 42 / .04), 0 1px 2px -1px rgb(15 23 42 / .02)` |
| Raised (menus, hover) | `0 10px 15px -3px rgb(15 23 42 / .07), 0 4px 6px -4px rgb(15 23 42 / .03)` |
| Tile / inset | `#F1F5F9`, radius 8px |
| Hairline / divider | `#E2E8F0` / `#EEF2F6` |
| Text | headings `#0F172A`, labels `#64748B` |
| Brand | text and buttons `#047857`, fills and progress `#059669`, live dots `#10B981`, hover `#065F46`, tints `#ECFDF5` / `#D1FAE5` |

Semantic pills (background / text / border / dot):

| Tone | Values |
| --- | --- |
| Success | `#ECFDF5` / `#065F46` / `#A7F3D0` / `#10B981` |
| Warning | `#FEF3C7` / `#92400E` / `#FDE68A` / `#F59E0B` |
| Danger | `#FFE4E6` / `#9F1239` / `#FECDD3` / `#E11D48` |
| Info | `#EFF6FF` / `#1E40AF` / `#BFDBFE` / `#3B82F6` |
| Quarantine | `#F5F3FF` / `#5B21B6` / `#DDD6FE` / `#8B5CF6` |

**Deviation from the spec, for accessibility:** the spec puts white text on emerald-600 (`#059669`), which is 3.8:1. Buttons and green text use
emerald-700 (`#047857`, 5.5:1) instead; emerald-600 and -500 remain for fills, progress bars, dots and accents.

## Typography

Plus Jakarta Sans for everything, JetBrains Mono for lot codes, batch IDs, AWBs and instrument readings. Tabular figures for counts, money and weights.

| Level | Size / weight | Class |
| --- | --- | --- |
| Display (screen title) | 28px / 800, -0.025em | `.t-page` |
| Section / card title | 16px / 700 | `.t-section` |
| Metric | 28px / 800 | `.t-kpi` |
| Body | 14px / 500 | body |
| Secondary | 13px / 500 | `.t-label` |
| Caption, micro-label | 11px / 700, uppercase, +0.06em | `.label-caps`, `.eyebrow` |
| Mono | 12–13px / 600–700 | `.font-mono`, `.tile-value` |

## Components

- **Buttons**: 8px corners. Primary emerald (`.btn-primary`), secondary white with a slate-300 border (`.btn`), destructive soft rose (`.btn-danger`),
  urgent solid rose (`.btn-urgent`).
- **KPI card** (`.kpi-card`): white card with a 3px accent top edge (`--kpi-accent`), uppercase label, icon in a soft square (`.icon-sq`), 28px figure
  with its unit, tinted chips, optional sparkline or segmented bar.
- **Status pill** (`.badge.tone-*`): tinted background, deep text, matching border, 6px dot; running states add `.badge-live` (pinging dot).
- **Tag** (`.tag`): uppercase pill for classifications and plant labels; `.tag-mono` for codes, `.tag-sim` for simulated content.
- **Tile** (`.tile`, `.tile-label`, `.tile-value`): slate-100 box with an uppercase label and a mono value, for instrument and record fields.
- **Data table** (`.dt`): slate-50 sticky header, uppercase 11px labels, 56px rows, slate-50 hover; codes in mono emerald.
- **Workflow stepper** (`.step`, `.step-active`, `.step-warn`, `.step-danger`): flat connected stages; the current stage gets the mint edge and wash.
- **Hero band** (`.hero`): white-to-mint wash for the Command Center header and summary bars.

## Screens

1. **Operations Command Center** (`/`): hero with quick actions, KPI cards, live lines, omnichannel pipeline, Meta and cleanroom panels, revenue and
   invoice charts, production output, needs attention, order-to-dispatch stepper, transactions, top customers, approvals.
2. **Production & Batch Runs** (`/m/production`): stat tiles, batch cards, equipment cards, dispensing and lot traceability table.
3. **Material Procurement & Stock Matrix** (`/m/procurement`): summary bar, capital cards, priority replenishment, inbound GRN tracker, material table,
   vendor on-time, spend by class, MRP card.
4. **Omni-Channel Dispatch & Invoicing** (`/m/ecommerce`): channel cards, dispatch velocity, five-stage pipeline, live queue, courier SLA, weigh-scale guard.

## Simulated content

The application has no sensor, SCADA, courier-SLA or weigh-scale integration. Readings of that kind (plant OEE, water index, vessel and equipment
telemetry, cleanroom environment, channel and courier SLAs, weigh-scale discrepancies, the MRP forecast note) live only in
`frontend/lib/demo-telemetry.ts`, are never stored or used in a calculation, and every panel or tile that shows them carries a visible "Simulated" or
"sim" mark. Everything else on these screens comes from the database through the API.

## Where it lives

`frontend/app/globals.css` (tokens and classes), `components/ui.tsx` (PageHeader, Badge), `components/blocks.tsx` (KPI cards, stepper, exception list,
charts), `components/fx.tsx` (sparkline, area chart, ring, ranked bars, count-up), `components/command.tsx` (Command Center panels),
`components/screens.tsx` (screens 2–4), `components/ModuleKit.tsx` (record cards, tables, report cards). Backend summaries for the screens:
`backend/app/module_dashboards.py`.
