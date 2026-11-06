# Demo Scenarios

What `init` creates, in the order a presenter would walk it. Each row names the screen/API to open. Dates are relative to the day `init` ran.

## Scenario A: one product, end to end (Oud Body Wash 200 ml, `FG-BW-OUD-200`)

| Stage | What exists | Open |
|---|---|---|
| Buy raw materials | POs to Himalayan Botanicals / Coastal Oils / Prime Pack (aloe, surfactant, Oud oil, bottles), approved by the admin; PR approved and rejected examples | Procurement -> Purchase orders |
| Receive + incoming QC | goods receipts with supplier lots, each batch in QUARANTINE; INCOMING inspections with a sampling plan (root n + 1), results by the inspector, release by a different quality manager | Quality -> Inspections; Inventory -> Batches |
| Make | manual BOM (with bottles) approved by the Production Head; order released (materials reserved), issued, 3 stages run by the operator, completed at 98% yield | Production -> Orders |
| Finished QC | FINISHED inspection (appearance, pH) -> released; **Certificate of Analysis** issued (frozen snapshot) | Quality -> Certificates |
| Sell | lead -> qualified -> converted to customer -> opportunity -> quotation (trade pricing) -> accepted -> won -> sales order (100 units) | CRM -> Pipeline; Sales -> Quotations |
| Ship in two parts | **60 units** shipped first (2 packages), **40 units** later; each shipment has its own invoice; together they equal the order total | Dispatch -> Order -> Shipments |
| Deliver | shipment 1: one failed attempt ("salon closed"), then delivered with a signed proof of delivery; shipment 2 is on the road | Dispatch -> Shipment detail |
| Get paid | invoice 1 paid in full and matched on the bank statement; invoice 2 open | Accounts -> Receivables |
| Trace | open the finished batch: raw-material lots, supplier receipts, order, shipments, customers (`/production/trace`, batch page) | Inventory -> Batch |

Caveat: the finished product's BOM is manual. The R&D formulation -> approved BOM -> pinned order chain is shown on the bulk intermediate `BLK-OUD-WASH` (Scenario D); bulk-into-finished consumption is not modelled.

## Scenario B: work in progress you can advance live

| State | Record | Next click |
|---|---|---|
| PO awaiting approval | PO for 300 kg surfactant (`PENDING_APPROVAL`) | log in as `demo.admin`, approve |
| Goods received, QC undecided | Shea butter lot (QUARANTINE, inspection partly tested) | `demo.qi` adds the missing result; `demo.qa` releases |
| Finished batch awaiting QC | Detan Face Mask batch (QUARANTINE) | complete the FINISHED inspection |
| Order waiting on that batch | confirmed order for Detan masks; allocation is refused because the batch is not released | after release, allocate |
| Packed, ready to dispatch | order of 50 Oud body scrubs (`PACKED`) | Dispatch (try a partial shipment) |
| Allocated | 30 Oud wash (`ALLOCATED`) | pack |
| Plan line waiting on materials | weekly plan, 1,000 Oud washes: material check is short of surfactant and bottles | approve the pending PO, then convert the line |
| Production in progress | bulk v2 order (stage 2 running) and a delayed Oud wash order (stage 1 running) | continue stages |
| Quotation in negotiation | Urban Spa: 3-outlet range, quotation sent | accept, mark opportunity won, convert |
| Overdue maintenance | filling-line quarterly service (due 8 days ago) | start the work order |
| Content pending approval | salon carousel post | `demo.admin` approves |
| CAPA done, not verified | vitamin E deviation | `demo.qa` verifies and closes |

## Scenario C: exceptions (all are real records, none hard-coded on a dashboard)

| Exception | Where it comes from |
|---|---|
| Material below reorder level | Oud fragrance 7.45 kg < 9; vitamin E 0 kg; Detan masks 0 available; Oud wash 6 < 20 (derived from balances and `reorder_level`) |
| Late supplier delivery | cartons PO expected 5 days ago, nothing received; vitamin E replacement PO |
| Delayed production order | Oud wash order planned 3 days ago, still running |
| QC-held / rejected batch | vitamin E lot REJECTED after a failed moisture test -> deviation (MAJOR) -> CAPA -> **purchase return** to the supplier (stock out, accrual reversed); a retest example keeps both readings |
| Shipment exceptions | one shipment in transit for 9 days; one delivery FAILED |
| Overdue customer invoice | 45-day-old invoice on 30-day terms: 15 days overdue |
| Supplier invoices | one unpaid, one half-paid, one paid; a short-supply **debit note** |
| Customer return | 2 damaged units returned and restocked after QC, credit note issued, customer pays the reduced balance |
| Unmapped marketplace SKU | Nykaa order in the exception queue |
| Capacity overload | plan confirmed over the packing line's capacity with a recorded reason |
| PO amendment | glycerin PO raised from 200 to 240 kg, approved by a second person; the PO is also partly received |

## Scenario D: R&D versions

Project "Oud body wash: sulphate-free base" -> formulation v1 (ingredients total 100%) with trial and stability observations -> approved by the R&D Head -> BOM v1 -> bulk order completed. Revision v2 (adds glycerin, less Oud oil) -> approved -> BOM v2 supersedes v1 -> bulk order in progress. v1 remains on record and its order stays pinned to v1.

## Marketplaces, CRM, maintenance, marketing

* **Marketplaces:** four DEMO accounts, two sample orders each; importing the same file twice creates nothing new; Amazon and Nykaa orders fulfilled and delivered; an Amazon settlement (gross, commission, shipping and ad fees) received, reconciled and posted; a Nykaa return.
* **CRM:** won (converted customer with an order), open (negotiation), lost (reason on record), new lead; one overdue follow-up, calls/meetings/e-mail logged.
* **Maintenance:** four assets; mixer service completed with checklist and spare parts and verified; filling-line service overdue; homogeniser breakdown with downtime and a spare-part record.
* **Marketing:** two campaigns; five posts (published-manually, approved, rejected, pending, draft); engagement metrics.
