# Demo Control Centre

A presenter's console for showing the application working as one connected system: **`/admin/demo-control`**, opened from the "Demo mode" pill in the top bar. Only the demo administrator (`demo.admin`, shown as Kanika) sees it, and only in the demo environment.

## What it is, and what it is not

Every button runs the **real application** as a seeded demo user: it calls the same HTTP routes a person's screen calls, so the same validation, permissions, idempotency, ledger postings, stock movements, audit rows and activity events happen. It does not write business tables directly and there is no frontend-only sequence that changes a number on screen. Everything it creates is labelled **Demo** (activity badge, event `source`), and the simulated pieces (instrument readings, a supplier delay message, the courier) are labelled **Simulated**.

## Safety

* The controls exist only when **all three** hold: `MINTREE_DEMO_LIVE=true`, `MINTREE_ENVIRONMENT` is exactly `demo`, and the database name contains the word `demo` (`demo_live/guard.py`). Anything else returns `403` / "not available here".
* Reset, snapshot and the seeder refuse any database that is not a demo database, checked before they touch anything (`scripts/demo/guard.py`).
* A person without the demo-admin identity gets `403` on every control endpoint, even in a demo environment.
* Unknown action names are rejected (`422`) and never reach the queue.
* Limits: automatic activity stops at a volume cap until a reset.

## Layout

* **Order to cash, scripted**: Start, Pause, Resume, Next step, Retry step, Reset scenario, speed ×1 / ×3, a 15-step list that fills in as it runs with the plain-English result and the time of each step.
* **Action groups**: Sales and customer orders; Procurement; Production; Quality; Accounts; Dispatch; Marketplace. Each button queues one real action; the result (or the reason it could not run) appears under **Your actions** on the right.
* **Live activity** (right): the committed events as they happen.
* **Reset demo data**: restores the golden snapshot in seconds; you stay signed in.

## Actions (all through the real services)

| Group | Action | What really happens |
|---|---|---|
| Sales | New B2B order | customer + finished good with allocatable stock; order created and confirmed |
| | Marketplace order | an import through the marketplace order code (SKU mapping, duplicate protection, sales order) |
| | Confirm an order / Cancel a demo order | confirm a draft; cancel an open demo order (reservations released) |
| Procurement | Create purchase request | for a low-stock material; submitted for approval |
| | Approve purchase request | approved by the administrator (never by the requester) |
| | Issue purchase order | from the approved request; submitted and approved (a second approver if the value needs one) |
| | Receive materials | goods receipt against the newest open PO; incoming QC inspection opened |
| | Simulate delayed delivery | a **Simulated** supplier alert to buyers; no order, date or stock changes |
| Production | Start production | production order from an active BOM whose materials are in stock; released; materials issued |
| | Advance one step / Complete production | stages started and completed (a failing in-process reading is re-checked first, as the plant would); finished goods land in QC hold |
| Quality | Record inspection | test results recorded by an inspector |
| | Pass inspection | results recorded; a different person releases (segregation of duties) |
| | Fail inspection | a value out of limits; the batch is placed on **HOLD** and leaves available stock |
| | Release a held batch | a new inspection, all mandatory tests pass, released by a second person |
| Accounts | Create draft invoice | a draft: nothing posted, no invoice number |
| | Post invoice | validate then post: goods ship, gapless invoice issued, journal balanced |
| | Generate invoice PDF | the newest invoice's PDF (brand layout) is produced and checked |
| | Record and allocate payment / Large payment | payment against an open invoice, allocated, status PARTIAL or PAID |
| | Process return and credit note | a return received and restocked; the credit note is posted |
| Dispatch | Dispatch an order | confirm, allocate, pack, dispatch, book the (simulated) courier |
| | Add tracking update | picked up, in transit, out for delivery |
| | Mark delivered | delivery event; shipment and order become DELIVERED |
| | Delivery exception | a failed delivery attempt with a reason; a high-priority alert |
| Marketplace | Import Amazon / Flipkart / Myntra / Nykaa order | one DEMO order through the real import |
| | Demo synchronisation | one order from each platform |
| | Failed import / Retry failed import | an unmapped SKU parks the order in the exception queue; mapping the SKU and retrying creates the order |
| | Duplicate delivery | the same order sent again: ignored, exactly one internal order |

## The scripted scenario

Fifteen steps, each a real operation. The demo sells a dedicated product (**Mintree Gift Set (Demo)**, created once through the master-data, quality and BOM APIs) so the story can be true: the seeded catalogue has huge stock from 25,000 orders of history, while this product has a small honest stock position and a bill of materials that needs more of one low-stock raw material than is on hand.

1. A new B2B order arrives (more than is on hand) and is confirmed.
2. It appears on the Sales and Dispatch dashboard.
3. Inventory is checked; a production order is planned; material availability is read.
4. A purchase request is raised for the missing material.
5. The request is approved; a purchase order is issued and approved.
6. Materials are received (stock, supplier history, incoming QC).
7. Incoming inspection passes; the batch is released.
8. Production is released and materials are issued.
9. Stages run; output is recorded into a finished batch held in QC.
10. The finished batch's tests are recorded.
11. A second person releases it; it moves to the dispatch warehouse.
12. The order is allocated and packed; **its invoice is drafted, validated and posted** (the goods ship).
13. Delivery is confirmed; the invoice PDF is generated and verified.
14. The customer pays; the payment is allocated.
15. The management dashboard is read; the invoice is PAID and the ledger balances.

Steps that are genuinely unnecessary on a given run (for example no shortage) are marked "not needed this time" rather than faked. A step that cannot run stops the scenario with the reason; **Retry step** or **Reset**. Pause is honoured even while a step is running (a lost-update race here was found and fixed by the black-box test). Speed ×3 runs the whole thing in about 40 s; ×1 in about 80 s.

While a scenario runs, the automatic Live Mode stays quiet so the story is the only activity.

## Running it

```
python -m scripts.demo_stack up        # API (1 worker on Windows), publisher, worker, live engine, web
open http://localhost:3101 -> demo.admin@example.com -> Demo mode pill -> Open the Demo Control Centre
python -m scripts.demo_ops reset       # back to the golden snapshot at any time
```

Take a new golden snapshot (`python -m scripts.demo_ops snapshot`) after any migration, otherwise a reset restores the old schema.

## Tests

`tests/demo/test_control_centre.py` (10, black-box against a running demo stack) and the browser run `tests/demo/ui.mjs` (9 steps). Results: `REALTIME_TEST_RESULTS.md`.
