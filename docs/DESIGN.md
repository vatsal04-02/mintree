# Mintree Factory OS: design document

The current visual and interaction design of the Mintree Factory OS web app (Next.js 16, Tailwind v4, React 19), as built in November 2026 for the
stakeholder demo. It replaces the earlier "Botanical Precision" write-up in `DESIGN_SYSTEM.md`, which describes a look the app no longer has.

Source of truth in code: `frontend/app/globals.css` (tokens and classes; the last block, "Rapport visual language", wins over every earlier one),
`frontend/components/` (shared components) and `frontend/lib/format.ts` (all number, money and date formatting).

---

## 1. Design intent

Mintree makes cosmetics. The app is the single record from formulation to delivery: purchasing, stores, production, quality, dispatch and accounts.
The design should feel like the brand (warm, natural, calm) while staying fast to read on a factory or office monitor.

Principles:

1. **Calm before clever.** One soft background, one card style, one control style. Colour is rare, so when it appears it means something.
2. **Colour carries meaning, never decoration.** Green is good or done, orange is a problem, yellow is waiting or needs review, ink (near black) is
   neutral or primary action.
3. **Tables stay tables.** High-volume lists (orders, ledger, batches) are dense, sortable tables. Cards and charts are for summaries and
   exceptions.
4. **Nothing is invented.** Every figure comes from the API. Content that is simulated (instrument readings, courier SLAs, the demo courier, the MRP
   note) is always visibly marked "Simulated" or "sim".
5. **Same shape everywhere.** Every module landing page is laid out by the same rules (section 9), so a new module looks finished on day one.

---

## 2. Visual language

### 2.1 Overall look ("Rapport")

- **Background:** a soft mesh, not a flat colour. Light: peach and cream at the top fading through cream to sage at the bottom right
  (`linear-gradient(135deg, #eddecd, #f6f0e6 48%, #d9e1d2)` with a warm yellow glow at the top centre). Dark: charcoal
  (`#101212` to `#1b1f1d`) with a faint warm glow.
- **Surfaces:** frosted cards (`.panel`, `.hero`). Background `rgb(243 241 235 / 0.84)` with a 14px backdrop blur, a 1px white-ish border, **28px
  corner radius** and a long, warm, low-opacity shadow. Inner tiles use a 16px radius, record cards 22px.
- **Controls:** everything is a **pill** (fully rounded): buttons, inputs, segmented toggles, status pills, round icon buttons.
- **Weights:** only 400 and 500. Nothing is bold. Emphasis comes from size, colour and spacing.
- **Sidebar:** unchanged deep teal (kept deliberately), see section 5.

### 2.2 Colour

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--ink-r` (ink) | `#151717` | `#f1efe9` | Text, primary buttons, neutral chart bars, progress fills |
| `--acc-green` | `#2B5C4E` | `#3F8A74` | Good, done, released, paid; chart "actual"; rate rings |
| `--acc-orange` | `#C6431D` | `#D9582F` | Problems, overdue, rejected; the latest bar in a chart; negative numbers |
| `--acc-yellow` | `#EFC84B` | `#EFC84B` | Waiting, review, quarantine, warning chips |
| `--card` | `#F6F4EF` | n/a | Opaque card base under the frosted layer |
| `--muted` | ink at 56% | `#F1EFE9` at 60% | Secondary text, labels, axis ticks |
| `--line` / `--line-strong` | ink at 8% / 15% | white at 9% / 18% | Hairline dividers, control borders |
| `--brand` | `#2B5C4E` | brand teal | Links, focus, selection |
| Sidebar | `#015A52` (active `#024840`) | `#072B26` (active `#0D4A42`) | Navigation only |
| Brand teal (logo) | `#01877A` | same | The Mintree wordmark on the login page and in the sidebar |

Semantic tones (`.tone-*` for status badges): success green on a 12% green tint, warning dark gold on a 32% yellow tint, danger deep orange on a
12% orange tint, info and neutral ink on a 6 to 7% grey tint. Each badge has a 6px dot in the tone colour.

Chart colour rules:

- Money series: orange line with a soft orange fill. Previous period: dashed ink line.
- Count series: ink bars, with the most recent non-zero bar in orange.
- Production planned vs actual: grey (planned) beside green (actual).
- Stage and status breakdowns are coloured by meaning: done and released green, problems orange, waiting yellow, everything else ink (alternating
  with a mid grey so neighbours are distinguishable).
- Stock value by class: finished goods green, raw materials ink, packaging yellow, bulk grey.

### 2.3 Typography

- **Font:** Outfit (variable), self-hosted via `@fontsource-variable/outfit`. Monospace (JetBrains Mono) only for codes, batch numbers, SKUs and
  quantities in tables. Figures use tabular lining numerals so columns align.
- **Scale** (rem at 16px base):

| Role | Class | Size / line | Weight |
| --- | --- | --- | --- |
| Page title | `.t-page` | 1.625 / 1.75 | 500, letter-spacing -0.02em |
| Hero figure | `.t-stage` | 1.5 / 1.75 | 500 |
| KPI figure | `.t-kpi` | 1.1875 / 1.5 | 500 |
| Card title | `.t-section`, card `h2` | 0.875 to 0.9375 / 1.375 | 500 |
| Body and table cells | body | 0.8125 / 1.25 | 400 |
| Label and support text | `.t-label`, `.t-support` | 0.75 to 0.8125 | 400, muted |
| Pill and caption | `.pill`, chips | 0.6875 to 0.75 | 500 |

- No uppercase tracking labels: eyebrows and table headers are sentence case, 0.75rem, muted.
- Names typed in capitals in the data ("COCOA BUTTER") are shown in title case; acronyms (PO, RFQ, SKU, GST, QC, CAPA, COA, RTO) stay upper case.

### 2.4 Iconography

Lucide icons only, **1.5px stroke**, 14 to 18px. Icons sit inside a 40px white circle (`.kpi-ic`) beside KPI figures, or inside a 36px outlined
circle (`.ctl`) for "more" and "open" controls. Icons are always decorative (`aria-hidden`) when a text label is present.

### 2.5 Spacing, radius, elevation

- Page gutter 16px on phones, 28px on tablets, 32 to 40px on desktop; content max-width 1500px.
- Gap between cards 16px; inside a card 18px horizontal padding, record cards 14px.
- Radius scale: 999px (controls), 28px (cards), 22px (record cards), 16px (tiles, text areas), 14 to 18px (chart segments).
- Elevation: cards sit on a wide soft shadow (`0 24px 60px -20px` warm brown at 18%); hovered cards and record cards lift 2 to 4px with a deeper
  shadow. Drawers and the toast use a heavier shadow.

---

## 3. Components

| Component | Behaviour and look |
| --- | --- |
| **Button** `.btn` | Pill, 36px high, translucent white with a hairline border. Primary `.btn-primary` is solid ink with white text (inverted in dark mode). `.btn-danger` is a soft orange tint. `.btn-ghost` has no chrome. `.btn-sm` is 30px. Press scales to 0.96. |
| **Input** `.input` | Pill, translucent white, hairline border; focus turns the border ink and adds a soft 3px ring. Text areas use a 16px radius. |
| **Segmented toggle** `.seg` | Grey track, white active pill with a small shadow. Used for period (30 days, 90 days, This FY, Custom), All/Unread, view switches. |
| **Status badge** `.badge.tone-*` | Soft tinted pill with a leading dot. Statuses are always words ("Pending booking", "Released"), never colour alone. |
| **Pill** `.pill-*` | Solid, small pill used for severity and chips: green, yellow, orange, grey. Examples: "Act now", "Review", "3 past planned date". |
| **KPI chip** (`Strip` in `kpi.tsx`) | A single row of figures: icon circle, number that counts up on first paint, optional coloured pill, and a small caption. It is a labelled region containing a list. |
| **Card** `.panel` | Frosted surface with a header row (title and optional meta line on the left, round "more" and "open" controls on the right). |
| **Record card** (`DealCard`) | 22px radius, 165px tall: meta pill and "..." at the top, title, then the figure at the bottom. Variants: white (default for attention lists), black, orange, green, yellow (pipeline stages). Staggered fade-up on load; lifts on hover. |
| **Donut** | Rounded-end arc on a track, percentage in the middle. Dark variant (yellow arc) or light variant (green arc on a light track). The arc is shortened by its stroke width so the rounded ends never touch. |
| **Stage mix** | A row of rounded segments sized by share, with a legend below (dot, label, share, count). |
| **Bar list** | Label and value on one line, a rounded 8px track below with a coloured fill that grows from the left. |
| **Column chart** | Slim ink columns with the value above each, labels below, the peak in orange. Used for time buckets such as expiry by month. |
| **Area chart** | Smooth line with a soft fill, four grid lines, hover readout, optional dashed previous period. Axis ticks are compact (₹4.5L, ₹1.5L). |
| **Data table** `.dt` | No fills, hairline rows, 52px rows (compact 44px), sticky header, sentence-case muted headers, right-aligned tabular numbers, hover wash, a 3px brand edge on the first cell on hover. Sortable columns show an arrow; long text truncates with the full text in a tooltip. |
| **Drawer** | Frosted panel sliding in from the right over a blurred, dimmed page. 1.5rem title, round close button, footer actions separated by a hairline. Only the topmost drawer closes on Escape. |
| **Toast** | Black capsule, bottom centre, check or alert icon (yellow or orange), 4 seconds (errors 6). |
| **Confirm dialog** | Centred panel for destructive or irreversible actions. |
| **Empty state** | Title, one line of help, optional action. Never a blank panel. |
| **Skeleton** | Pulsing block with the same radius as what it replaces, to avoid layout shift. |
| **Tags** | `.tag` pill for classifications; `.tag-mono` for codes; `.tag-sim` (yellow) for simulated content. |

---

## 4. Motion

Library: Framer Motion, plus CSS transitions for small state changes. Principles: motion explains cause and effect, stays under about 0.9 seconds, and
is switched off for people who ask for reduced motion (`prefers-reduced-motion`).

- **Page entry:** content fades and rises a few pixels; cards below the fold animate when scrolled into view (`InView`).
- **Numbers:** KPI figures count up on first paint (`CountUp`), skipped when reduced motion is on.
- **Charts:** bars grow from the baseline (about 0.4s, staggered up to 0.6s); area lines draw in; donut arcs sweep over 0.9s; progress bars grow along the
  x axis.
- **Record cards:** staggered fade, rise and de-blur (0.45s, 55ms apart). Hover lifts 2 to 4px. Pipeline cards can be dragged, with a lift and a slight tilt.
- **Controls:** press scales to 0.96 in about 120ms; segmented toggles slide their active pill.
- **Login:** the product shelf fades in, drifts opposite to the pointer, and a spotlight follows the pointer.
- **Toast and drawer:** slide and fade in; backdrop blurs in.

---

## 5. Layout and navigation

### 5.1 App shell

- A floating **deep teal sidebar** (244px, collapsible to 60px; the choice is remembered) on the left, and the content area to its right on the
  mesh background. On phones the sidebar becomes a 260px drawer opened from a menu button, with a dimmed backdrop.
- Sidebar groups, in order: Workspace (Overview, Approvals, Notifications), Operations (Purchase, Inventory, Production, R&D, Quality, Dispatch,
  Maintenance), CRM (CRM & Sales), Finance (Accounts, Management), Integrations (E-commerce, Meta & Branding), Master data, Assistant (Document
  reader, Suggestions), Records, Administration. Groups open to show screens; the current screen has a left edge marker and a lighter pill. Items the
  user has no permission for are hidden.
- **Signed-in card** at the bottom of the sidebar: round photo or initials, name, role line.
- **Top bar** (64px, transparent): menu button (mobile), breadcrumb, then Scan (barcode, Alt+S), Assistant (Ctrl+J, only when the user may use it),
  notification bell with an unread count in a yellow badge, a search pill that opens the command palette (Ctrl+K), the black "New" menu, and the
  profile menu (name, email, department and roles, light/dark appearance, match system, sign out).

### 5.2 Page anatomy

Every page follows the same top-to-bottom order:

1. **Header:** page title (and one muted line of description), actions on the right (secondary pill buttons, one black primary).
2. **KPI chips:** one row, up to five figures.
3. **Lead row:** the main chart or attention list on the left (8 of 12 columns), a rate card and a short list of figures on the right (4 columns).
4. **Working surface:** tables and breakdown cards.
5. **Links to the module's other screens** as quiet cards at the bottom.

Grid: 12 columns from the xl breakpoint; one column on phones; two on tablets. Cards in a row stretch to the same height.

### 5.3 Responsive behaviour

- Phones (390px): single column, 16px gutters, no horizontal page scroll (checked by an automated audit across 90 page views), tables scroll inside
  their card, touch targets are at least 44px.
- Tablets: two columns, sidebar as drawer.
- Desktop (1440px reference): full 12-column layout. Content never exceeds 1500px.

---

## 6. Screens

### 6.1 Sign-in

A full-bleed photograph of the Mintree product range sits behind the page at about 26% opacity under a warm wash. On desktops a circular spotlight
follows the pointer and reveals the products in full colour, the whole shelf drifts slightly with the pointer, and five dots name the products on
hover (Oud-One-Out Body Wash, Estonian Peat Detan Face Mask, Peat Face Mist, Fall Stop Shampoo, the refillable dispenser). On the left, the green
Mintree wordmark, the headline "From formulation to refill pouch." and a one-line hint. On the right, a frosted sign-in card with pill fields and a
black button. On touch screens and for reduced motion the shelf simply rests behind the form.

### 6.2 Overview ("Factory overview")

- Greeting by the viewer's clock ("Good morning, Kanika"; morning from 5 to noon, afternoon to 5 pm, evening after), then the title on one line and
  "Showing 14 Oct - 12 Nov 2026".
- Period toggle (30 days, 90 days, This FY, Custom) and a black **Approvals** button with the count.
- Four KPI chips with icons: receivables outstanding, net revenue (with change versus the previous period), active production orders (with a "past
  planned date" pill), inventory alerts.
- **Revenue** area chart (orange, previous period dashed) beside a light **Plan attainment** card with a green ring and Made / Planned / Short.
- **Needs attention:** white record cards sorted by severity, with "Act now" (orange value) and "Review" (yellow) pills.
- **Production output** (planned vs actual by week) beside **Order to dispatch** (stage mix).
- **Recent transactions** table and **Pending approvals** list.
- A role without the analytics permission gets an honest notice instead of zeros.

### 6.3 Module landing pages (generic dashboard)

R&D, Quality, Dispatch, Maintenance, CRM & Sales, Accounts, Meta & Branding, Management and the CRM pipeline summary are drawn by one component from
the shape of the API response (section 9).

### 6.4 Purpose-built screens

- **Purchase:** KPI chips (inventory valuation, low-stock materials, open purchase orders, overdue inbound), "Priority replenishment" and "Inbound
  purchases" tables stacked at full width, and a **Material matrix** table with class filter, search, low-stock filter, sortable columns and
  pagination.
- **Inventory:** KPI chips; weekly batches chart beside stock value by class; **Reorder watch** table (cover bar, on order, status, Request button);
  **Warehouses** table; **Latest stock movements** and **Expiring soonest** tables; breakdowns for QC status, movement types and top items by value.
- **Production:** summary chips, batch cards, equipment table (with simulated telemetry marked), dispensing table.
- **E-commerce (Omni-channel):** channel strip, pipeline rail, dispatch queue, courier list.
- **CRM pipeline board:** four tinted columns (Prospecting ink, Qualified yellow, Proposal green, Negotiation orange); draggable coloured deal cards with a
  move menu; dashed Won and Lost drop zones; KPI chips (open pipeline, weighted, closing this month, win rate); search pill; Board/List toggle.

### 6.5 List screens (tables first)

Every master and transaction list uses `ListScreen`: a status tab strip with counts, search, table by default (sortable, 50 per page, empty columns
hidden), optional card/board view for procurement (RFQ and quotations, purchase requests, purchase orders, returns), marketplace orders, production
orders, work centres and BOMs. Row actions sit on the right; clicking a row opens a record drawer.

### 6.6 Other pages

- **Notifications:** alerts grouped by Today, Yesterday and Earlier; each is a rounded row with a kind icon (cart, shield, box, document, bell), an
  orange dot when unread, title, optional body, relative time, an "Open" button (which also marks it read) and a round tick. All/Unread toggle and
  "Mark all read".
- **Approvals, Reports, Admin, Master data:** same header, KPI-less list or report layout as above; reports use hairline grids and titled cards.

---

## 7. Data presentation rules

- **Numbers and dates** are formatted only in `lib/format.ts` (an ESLint rule bans `.toFixed` and direct `Intl` use elsewhere). Locale is **en-IN**:
  lakh and crore grouping (`₹37,43,969`), compact chart ticks (`₹4.5L`, `₹1.2Cr`), dates as `12 Nov 2026`, short dates as `12 Nov`, relative times
  ("12 min ago", "yesterday") with the exact time in a tooltip. A value that rounds to zero never shows a minus sign.
- **Money** is always shown with the rupee symbol; quantities carry their unit in lower case (`99 kg`, `2,174 pcs`).
- **Labels** come from field names, with acronyms kept upper case and "follow-ups" spelled with a hyphen. Day windows go in brackets ("Expiring within
  90 (days)").
- **Empty and zero:** a zero is shown as 0 when it is a real measurement and omitted when the section would be empty. Panels with nothing to show say
  so in one line.
- **Truncation:** one line with an ellipsis and the full text in a tooltip; multi-line text is clamped to two lines.

---

## 8. Accessibility

- **Contrast:** body text and muted text meet 4.5:1 on the card and the mesh in both appearances; control borders meet 3:1; status is never shown by
  colour alone (always a word or dot plus a word).
- **Keyboard:** every control is reachable; visible focus ring (3px soft ring, ink in light mode); Escape closes the topmost drawer or dialog; Ctrl+K,
  Ctrl+J and Alt+S shortcuts.
- **Semantics:** KPI rows are labelled regions with lists; charts have a `role="img"` with a text summary (peak, total); icon-only buttons have
  `aria-label`; toasts use `role="status"`; sort headers use `aria-sort`; the signed-in unread state is announced.
- **Touch:** controls grow to at least 44px on small screens; hover-only information is also available by tap or focus.
- **Motion:** `prefers-reduced-motion` disables lifts, count-ups, parallax and spotlight.
- **Colour appearance:** light and dark, remembered per browser or following the system. Dark swaps to the charcoal mesh, inverts the primary button,
  and re-tints pills and chips so they stay readable.

---

## 9. Module dashboard rules (how a landing page is composed)

`ModuleDashboard` reads whatever a module's dashboard endpoint returns and lays it out by shape, so every module is consistent:

| Data shape | Becomes |
| --- | --- |
| First four numbers | KPI chips with an icon chosen from the field name |
| A rate (name contains rate, pct, percent) | Light rate card with a green ring |
| Problem figures above zero (overdue, failed, delayed, rejected, breakdown, expired, exception, unmatched, RTO, lost) or review figures (awaiting, pending, quarantine, due) | "Needs attention" record cards, or a compact list when there are one or two |
| Remaining numbers | "At a glance" list beside the lead card, or a grid when there are many |
| `*_trend` series | Hero chart: money as an orange area line, counts as ink bars with the latest in orange; further series as smaller cards |
| `{label: number}` | A stage-mix card for the first status or stage breakdown; coloured bar lists for the rest; column charts for time buckets |
| `{group: {a: n, b: n}}` | A bar list whose rows also show the extra figures (count and value) |
| Text notices | A banner with a yellow "Note" pill (used for simulated-data warnings) |
| Anything else | The generic report view |

Layout is chosen so no row is left half empty: the hero chart grows to match the side column; one or two attention items become a list beside the
chart; breakdown cards flow in columns so short cards do not leave holes.

---

## 10. Content and tone

- Plain, short, factual. Titles are nouns ("Reorder watch", "Latest stock movements"). Actions are verbs ("Request", "Mark all read", "Start stocktake").
- No marketing language inside the app. Descriptions are one sentence.
- Severity words are consistent: **Act now** (orange), **Review** (yellow), **Monitor** (grey).
- Anything not backed by a live system is labelled **Simulated** (instrument telemetry, courier SLA, weigh-scale readings, the fake courier shown as
  "Simulated courier", the MRP forecast note, sandbox Meta metrics). Simulated values live in `lib/demo-telemetry.ts`, are never stored and never feed
  a calculation.

---

## 11. Brand and assets

- **Wordmark:** the Mintree script logo (`components/Logo.tsx`, SVG that paints with `currentColor`). Brand teal `#01877A` on light backgrounds and
  on the login page, white in the sidebar. A cropped "M" mark is used when the sidebar is collapsed.
- **Photography:** the product range photograph for the sign-in background (`public/brand/products.jpg`).
- **Demo identity:** the demo administrator account is presented as **Kanika, Founder, Mintree** with a round portrait (`public/avatars/kanika.jpg`) in
  the sidebar card, the top-right avatar and the profile menu. This is display only, defined in `lib/persona.ts`; the login, roles and audit trail
  are unchanged.
- **Tab icon:** `app/icon.svg`.

---

## 12. Technology notes for designers and developers

- Next.js 16 (App Router), React 19, Tailwind CSS v4, Framer Motion, Lucide, Outfit via `@fontsource-variable`.
- Styling order matters: later unlayered rules in `globals.css` override Tailwind utilities, and the Rapport block is last. Change tokens in that block
  first; avoid hard-coded hex in components (use `--ink-r`, `--acc-*`, `--line`, `--muted`).
- Shared building blocks: `components/ui.tsx` (PageHeader, Badge, Drawer, toast, confirm, empty state), `kpi.tsx` (Strip), `rapport.tsx` (CardHead,
  DealCard, Donut, StageMix), `fx.tsx` (AreaChart, CountUp, InView), `ModuleDashboard.tsx` (generic landing page), `ModuleKit.tsx` (ListScreen,
  ReportScreen, record drawer), `screens.tsx` (Purchase, Production, Omni), `inventory.tsx` (Inventory), `AppShell.tsx` (sidebar and top bar).
- Checks that guard the design: the page audit (no horizontal overflow, console errors or broken pages across 90 views), the Overview and full demo
  end-to-end tests, ESLint rules for formatting, and the type checker.

---

## 13. Known gaps and next candidates

Not yet restyled in the Rapport language: the Customers directory and detail pages, the coloured record modal, the Finance cash-flow and ageing cards
and the invoice paper drawer, the growth charts, and a fuller tab-switch motion choreography. The generic report view still renders nested figures
as plain tiles in a few places.
