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

- **Page entry (choreography):** the page cross-fades and de-blurs over 200ms; the title wipes in (450ms); each card arrives as a pale ghost and then fills (fade, rise 14px, de-blur 8px, 450ms, `cubic-bezier(.22,1,.36,1)`), 55ms after the previous one; KPI figures count up from a fifth of their value over 900ms; charts draw (lines 900ms, bars grow from the baseline). Total feel is about 1.1s. The previous page cannot stay on screen in the App Router, so there is no exit animation. Cards below the fold animate when scrolled into view (`InView`).
- **Sliding markers:** the active sidebar item and every `Segmented` tab group use a shared-layout marker (Framer `layoutId`, spring stiffness 380, damping 32) that glides to the new item.
- **Record modal:** a clicked record card grows into a centred modal in its own colour (about 450ms spring), the page behind blurs (20px) and dims, content fades in after 150ms, and closing folds it back into the card.
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

### 6.4a Customers, Accounts and Growth

- **Customers** (`/crm/customers`, CRM & Sales): a table by default (sortable, 24 per page) with a Grid alternative of customer cards, type chips with counts, search, Export CSV and a black "New customer" button. Each card shows initials, name, contact and state, type tag, an inset tile (open pipeline or invoiced, and last order), a thin bar and round mail and phone buttons. The bar is the share of the credit limit in use (or, with no limit, of invoiced revenue still unpaid): orange from 60%, green below. There is no score, tier or star because the database has none. The detail page (`/crm/customers/{id}`) shows KPI chips, a profile card (initials in a ring showing the paid share), contact and credit details, and the latest orders as record cards.
- **Accounts** (`/m/accounts`): KPI chips; a cash-flow chart (green money received, dashed money invoiced); receivables ageing as five tall rounded bars (ink, over 90 days in orange) with count and amount per bucket; the latest sales invoices with status badges. Selecting an invoice (or opening `?invoice=<id>`) shows a frosted drawer holding a paper invoice: logo, From and Bill to, lines with GST, CGST and SGST or IGST, totals, credit notes, balance due, and a rotated outlined green "PAID" stamp when paid. Actions: Copy link, Download PDF, and Record payment when unpaid. The lines come from the same query as the PDF.
- **Growth** (Management page): 3M, 6M or 12M toggle and Export CSV. "Invoiced by month" is a stacked bar (green collected, yellow not yet due, orange overdue, adding up to the invoiced total including GST); "Revenue by channel" is a donut with a legend (channels are not good or bad, so they are drawn in shades of ink); "Invoices per month" is a small bar chart with the latest month in orange.

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

## 6b. Interaction, storytelling, showpieces and finishing (phases 2 to 5)

### Quick actions and navigation
- **Dock** (from 1280px wide): a slim ink capsule at the left edge of the content area holding up to eight pinned actions (scan, approvals, new purchase order, start stocktake, new production order, new sales order, and more). The pencil at its foot chooses them. Only actions you hold the permission for are listed; the choice is remembered per user in this browser. Hover scales an icon and shows its name and shortcut. On phones the same actions are in the bottom bar's "More" sheet.
- **Command palette (Ctrl K)**: sections for Recent, Actions, Pages and Records; fuzzy matching; keyboard only (arrows, Enter, Esc); shortcut hints. Records come from items, suppliers and customers, plus the barcode resolver, so a batch label or an order or invoice number opens its record.
- **Pinned pages and recents**: a star beside the breadcrumb pins a page to the top of the sidebar; the last three pages visited are listed under it. The breadcrumb shows the module and a switcher to the module's other screens. Once you scroll past a page title it moves into the top bar.
- **Shortcuts**: `?` opens a sheet of every shortcut with a What's new tab (`lib/whatsnew.ts`). `g` then `o`, `a` or `n` goes to Overview, Approvals or Notifications.
- **Phones**: a bottom bar (Overview, Approvals, Scan in the centre, Alerts, More). The app has a web manifest and icons, so it can be installed to the home screen. The scanner opens full width on phones.

### Undo, peeking and tables
- **Undo**: approving or rejecting on a list, moving a deal between stages, and marking an alert read take effect on screen at once; the server is told after four seconds, and "Undo" in the toast cancels it, so nothing needs reversing. If the server then refuses (for example, you cannot approve your own request), the screen is put back and the reason is shown. Pending changes are sent at once if the tab is hidden or closed.
- **Peek**: opening a record from a list shows "3 of 50" with up and down buttons; `J`/`K` or the arrow keys step through the rows as filtered and sorted.
- **Tables**: row size (comfortable or compact), a column chooser, saved views (filter, search, sort, columns and row size), an optional totals row on screens with money columns, CSV export of exactly what is shown, and row selection with a floating bar (export or copy the selected). Keyboard: `J`/`K` move a cursor, `Enter` opens, `X` selects. Selection never changes a record.

### Telling the story with the figures
- **Insight lines** (`lib/insights.ts`): one plain sentence under the Overview figures, from fixed templates and numbers already on the page ("Net revenue is 15% above the previous 30 days, led by ... (21% of invoiced value)"). They are hidden when the data is too thin.
- **Trend lines and arrows**: a KPI chip can carry a small trend line (shown from 1600px wide) and an arrow in its pill. Colour follows meaning, not direction; a text equivalent is always present.
- **Morning brief**: "Needs you today" shows approvals waiting (count, oldest, largest), the most urgent act-now item and overdue invoices, each linking to its screen.
- **Linked crosshair**: hovering the revenue chart marks the same date on the production chart, and the reverse (`lib/chartSync.tsx`).
- **Drill-down**: bars and segments in a status breakdown link to the list they count, already filtered (`?status=`), so the address can be shared.
- **Chart pins**: real events appear as small pins on the revenue line: equipment breakdowns, failed deliveries and the two largest invoices of the period (`/analytics/annotations`). A period with none shows no pins.
- **Projection**: a dashed line and shaded range carry the recent revenue trend forward (a straight line fitted to the last three weeks, one spread either side). It is always badged "Projected", appears only when the period ends today, and feeds no figure (`lib/forecast.ts`).

### Showpieces
- **Expiry heatmap** (Inventory): batches still holding stock by expiry month and item class; past months orange, the next three months yellow, later months ink; darker means more batches.
- **QC control chart** (Quality): the readings of one numeric test in time order, with the mean, control limits at three spreads, and the specification limits; readings outside specification are orange. Switch between tests with the toggle.
- The batch genealogy graph and impact analysis already existed (Inventory, Batch traceability) and were not rebuilt.

### Finishing
- **QR on PDFs**: the invoice and the certificate of analysis carry a QR code. With `MINTREE_PUBLIC_BASE_URL` set it links straight to the record; without it, it holds the document or batch number, which the in-app scanner resolves.
- **Notifications**: Open now goes to the real screen with the record opened (it used to link to an API address); Snooze hides an alert for an hour or until 9:00 tomorrow (this browser only), with Undo.
- **Empty states**: a one-colour line drawing (tray, check or search), one sentence and, where it helps, one action.
- **Speed**: palette results prefetch their page when hovered or focused.

---

## 6c. Demo mode and live updates

Only in a demo environment (`MINTREE_ENVIRONMENT=demo`, a database whose name contains "demo", `MINTREE_DEMO_LIVE=true`); nothing below renders or runs anywhere else. Operating detail is in `DEMO_RUNBOOK.md`.

- **Demo mode pill** (top bar, left of the bell): a small outlined pill with a green dot, "Demo mode", or a yellow dot and "Demo mode · paused". For the demo administrator it opens the **Demo controls** drawer; for everyone else it is a quiet label.
- **Demo controls drawer** (460px, the standard drawer): Pause / Resume live, a ×1 / ×3 segmented speed toggle, "Trigger now" (New order, Quality alert, Big payment, Low stock), a Server panel (requests per second, p95, error rate, DB connections, in flight, queue depth, six small tiles refreshed every 3 seconds), "Created this session" counts with the last few events, an **Instruments** list marked with the yellow **Simulated** tag (temperature, weigh scale, line speed per running batch), and a quiet red "Reset to golden snapshot" at the foot (asks first).
- **Live updates, never reloads.** One event stream (`GET /api/events`, Server-Sent Events) carries `order.created`, `payment.received`, `stock.changed`, `batch.stage`, `quality.alert`, `notification.new`, `kpi.invalidate` and `telemetry`. It is authenticated, filtered by the viewer's permissions, sends a heartbeat every 15 seconds, reconnects by itself and falls back to polling every 20 seconds. Screens re-read only what they show, 3.3 seconds after an event (a moment past the server cache), at most once every 1.5 seconds, and swap the figures in place: no skeleton, no flicker. The bell count updates at once and an urgent alert (HIGH priority) also shows a toast.
- **Simulated stays Simulated.** Instrument readings are generated by the stream, are labelled with the yellow Simulated tag wherever they appear, are stored nowhere and feed no calculation. Everything else Live Mode creates is a real record made through the real API and tagged `source: demo-live` in its event log.
- **Freshness.** Dashboards and boards are cached for at most 15 seconds and rebuilt within 3 seconds of any business write (`meta.generated_at` says when); the Overview's `?fresh=true` bypasses the cache.
- **Menus above neighbours.** A card's open "..." menu sits above the cards below it (the pipeline board's "Move to Won" was previously hidden behind the next card).

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
- Phase 1 additions: `components/motion.tsx` (`PageTransition`, `Reveal`, `SlideMarker`, `EASE`, `SPRING`, `STAGGER`), `RecordModal.tsx`, `customers.tsx`, `finance.tsx` (`FinanceScreen`, `InvoiceDrawer`), `growth.tsx`, `lib/csv.ts` (`downloadCsv`).
- **How to use `Reveal`:** wrap a card in `<Reveal index={i}>` for the ghost-then-fill arrival; plain `.panel.rise` cards get the same entrance from CSS. **`SlideMarker`:** render `<SlideMarker id="…" className="seg-marker" />` inside the active item of a group (items need `relative`); the same `id` makes it glide. **`RecordModal`:** give the card `layoutId={recordLayoutId(id)}` and an `onOpen`, hold a `RecordView` in state and render `<RecordModal record={view} onClose={…} />`; only the sections you pass (stage track, tiles, note, actions) are drawn. **`downloadCsv(name, header, rows)`:** writes raw values for a spreadsheet. **`useTopLayer(open, onEscape)`** (in `ui.tsx`): makes Escape close only the topmost drawer or modal.
- New endpoints behind these screens (all in `backend/app/module_dashboards.py`): `/inventory/hub`, `/crm/customers-board`, `/dispatch/invoices/{id}/paper`, `/analytics/growth`, `/analytics/annotations`, `/quality/control-chart`.
- Demo mode: `lib/live.tsx` (`LiveProvider`, `useLiveEvent`, `useLiveRevision`, `useLiveTelemetry`), `components/DemoControls.tsx` (`DemoPill`, the drawer). Backend: `app/demo_live/` (engine, store, ops, router), `scripts/demo_check.py`, `scripts/demo_ops.py`, `scripts/demo_prep.py`, `scripts/demo/bulk.py`.
- Phase 2 to 5 additions: `components/Dock.tsx` (`Dock`, `usePinned`), `ShellExtras.tsx` (`ShortcutSheet`, `MobileBar`), `TableTools.tsx` (`TableTools`, `BulkBar`), `quality.tsx` (`ControlChart`), `lib/actions.ts` (the action registry), `lib/prefs.ts` (`usePref`), `lib/insights.ts`, `lib/forecast.ts`, `lib/chartSync.tsx`, `lib/whatsnew.ts`, `lib/useOutsideClick.ts`; `public/manifest.webmanifest` and `public/icons/`.
- **How to add a quick action:** add an entry to `ACTIONS` in `lib/actions.ts` with its permission and address (`?create=1` opens a list's create form); the dock, palette and mobile sheet pick it up. **`usePref(name, default)`** keeps a per-user preference in this browser. **`useUndoable()`** (in `ui.tsx`): `undoable({ message, apply, revert, commit })` applies the change on screen, sends `commit` after four seconds, and calls `revert` on Undo or on failure. **`ChartSyncProvider`** wraps the charts that should share a hovered date. **`projectSeries(points, ahead, stepDays)`** returns a labelled projection; anything that draws it must say "Projected".
- **Table keyboard and tools** live in `ListScreen` (`ModuleKit.tsx`); `DataTable` takes optional `dense`, `selected`, `onToggle`, `onToggleAll`, `activeId` and `footer`.
- Checks that guard the design: the page audit (no horizontal overflow, console errors or broken pages across 90 views), the Overview and full demo
  end-to-end tests, ESLint rules for formatting, and the type checker.

---

## 13. Known gaps

Everything in phases 1 to 5 of the plan is built except the items below, each skipped because the data or the right to do it does not exist.

- **Exit animation** between pages: the App Router unmounts the old page, so only the entrance is animated.
- **Record modal on table rows**: only record cards open it. A deal's modal shows only what the API returns (no company, opened date, owner name or note).
- **Customer tier, score, star, avatars and a task schedule**; **Growth win-rate trend** (all stage changes fall in one month); **Mark paid** (a payment needs a bank account and method, so the drawer links to Payments).
- **Approve in a notification**: the decision depends on the record's current state, version, authority limit and who raised it, so Open takes you to the record where those are shown. Approve and reject on lists and Undo are built.
- **Undo for "mark paid"**: there is no mark-paid action to undo.
- **"Create PO for <material>" in the palette**: the purchase order form cannot be pre-filled, so the palette offers "New purchase order" instead.
- **In-cell sparklines in tables**: no list endpoint returns a time series per row.
- **Cost waterfall**: the costing report has no bulk, packaging, labour and overhead split per product (labour and overhead are not recorded), so a waterfall would be invented.
- **Production Gantt with drag to reschedule**: not built; rescheduling needs the production scheduling rules, which deserve their own design.
- **Warehouse bin map**: no bin capacity or fill data.
- **Product photography tiles**: there are no SKU images; the one product photograph is not mapped to items.
- **Batch manufacturing record PDF**: no such document exists yet; the invoice and certificate of analysis carry the QR.
- **Presence avatars** ("Rohan is viewing"): there is no presence channel.
- **View Transitions API**: left out; it needs experimental React support and the page entrance already cross-fades.
- **Chart annotations**: only the three event kinds above exist as records; price changes and shutdowns are not recorded with dates.
