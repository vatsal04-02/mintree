# Tier 1 operations: recall, expiry, rework, material return, shop floor, GST e-invoice

What was built, the rules each one enforces, how to use it, and what it does **not** do. Migration `0031`; run `python -m app.seed` once on an existing database to add the new permissions to the roles that carry them.

## 1. Batch recall (`/m/quality/recalls`, `/recall/<id>`)

One action from a suspect batch: the system walks everything that consumed it (through bulk intermediates and through rework outputs, using the existing genealogy), places every touched batch on **HOLD** through the same ledgered QC path a manual hold uses, lists every shipment that left in any of those batches, and opens a case.

* **Held, not destroyed.** A batch already on HOLD or REJECTED is recorded as it was and left alone. Reserved stock stays reserved but is flagged; the case lists the sales orders that hold it so someone re-allocates them. Nothing is cancelled for you.
* **Customers.** One line per shipment line, with order, AWB, batch and quantity. Mark each (or all pending) *notified*, *stock returned* or *cleared*. `Call list (CSV)` gives the person making the calls the customer's primary contact.
* **Closing.** Only when every customer line is dealt with **and** every batch the recall held is released (retested) or rejected, each with a note. A batch held by a recall can be resolved only through its case.
* **Who.** `quality.recall` read / create / notify / close (quality managers; management and others read). Starting a recall also needs batch read and QC release rights.
* **Not done.** Messages are not sent to customers (the call list is the output); regulator notification forms; a recall does not reverse invoices (use returns and credit notes).

## 2. Expiry control (`/m/inventory/expiry-control`)

* **Worklist** by urgency (expired, within 30, within 90 days) with quantity, value at moving-average cost, QC status, who has it reserved, and a suggested action.
* **Hold all expired stock** (sweep): expired batches that still count as released or quarantined stock are placed on HOLD (ledgered, audited) and the stores and quality people are notified.
* **Minimum remaining shelf life** for sales allocation: company setting `dispatch.min_shelf_life_days` (0 = off). Stock with fewer days left is not allocated to customer orders. Production reservations are unaffected.
* **Not done.** The sweep is a button, not a scheduled job; write-off of expired stock stays a stock adjustment with its own approval.

## 3. Material return and rework (`/m/production/rework`, order action, shop floor)

**Return unused material.** While an order is in progress, material the line did not use goes back to the very lots it came from, at the cost it left at, out of the order's WIP: ledger movement `RETURN`, valuation restored, a balanced journal (Dr inventory, Cr WIP), the order's output unit cost falls accordingly. Never more than was consumed from a lot; if a lot has since been held or rejected the returned quantity comes back blocked.

**Rework.** A held or rejected batch can be re-processed instead of scrapped: starting takes the blocked quantity out of the source batch (ledgered) and holds its cost; completing receives the good output into a **new** batch of the same item (same manufacturing and expiry dates: rework does not extend shelf life) that waits in quarantine for a fresh QC inspection. Loss is absorbed into the new batch's unit cost. A started rework can be cancelled (stock and cost go back). A later recall on the source batch reaches the rework output.

* **Books.** The inventory reconciliation rule now counts cost held in started rework orders, so GL equals valuation + WIP + rework at all times (tested).
* **Not done.** Rework labour/machine cost is not added (loss only); a rework cannot be split across several outputs; the rework's own inspection uses the item's existing specification (none = quarantine until QC decides).

## 4. Shop floor mode (`/floor`)

A tablet-first screen over the same production routes (same permissions, idempotency, audit): pick a batch; the next action is always the biggest button (issue materials, start stage, finish stage); record a reading with limits; return unused material; record the output (asks for wastage and a reason when yield is under 95%). Updates live when anything on the floor changes.

* **Not done.** Offline use, barcode scanning inside the floor screen (use Scan, Alt+S, for now), operator PIN login, equipment selection on stage start (use the desk screen when equipment tracking applies).

## 5. GST e-invoice and e-way bill (invoice list actions; `/api/accounts/einvoices`)

**Real:** the payload is built to the e-invoice schema (INV-01 v1.1) from the stored invoice and its shipment lines, so it always agrees with the ledger; it is validated before sending (company and buyer GSTIN format **and check character**, state codes, HSN 4 to 8 digits, 6-digit PIN, CGST+SGST vs IGST, line totals vs invoice total) and every problem is reported at once; the **IRN is computed as the portal defines it** (SHA-256 of supplier GSTIN, financial year, document type, document number); generating twice returns the same IRN; a cancelled IRN's document number is never reused; cancellation follows the 24-hour rule and is refused once an e-way bill exists; an e-way bill needs a generated IRN and a value over Rs 50,000. Results are stored per invoice and shown on the invoice PDF (IRN, acknowledgement, QR).

**Not real (and every screen, record and PDF says so):** registration with the government portal needs an account with a GST Suvidha Provider (or the NIC API) and its credentials; none exists here. The only provider is the **SANDBOX** one: real IRN, placeholder acknowledgement number, date and QR, marked "SANDBOX: not registered with any government portal; has no legal value". **LIVE mode refuses to run** until an adapter for your provider is written and configured. Production refuses to start in sandbox mode. `MINTREE_EINVOICE_MODE` = `off` (default) | `sandbox` | `live`.

**To go live:** choose a GSP, write `LiveProvider.register/cancel/eway` against its API with credentials from the environment, test against its sandbox, and set the company GSTIN, address and PIN in Administration settings. The validation, storage, idempotency and PDF already exist.

## 6. Demo

* Demo Control Centre: Start a batch recall, Resolve and close the recall, Run expiry sweep, Start rework of a held batch, Complete rework, Return unused material, Generate e-invoice (sandbox).
* `demo_prep` gives the company a valid GSTIN, address and PIN and gives 60 B2B customers well-formed GSTINs and PINs; the demo stack runs in sandbox mode.

## 7. Tests

`backend/tests/test_tier1.py` (16): GSTIN check character, the IRN definition, payload from the books, intra- and inter-state tax split, idempotent generation, every validation failure at once, the cancel window and number reuse, e-way thresholds, off/live refusal, production refusal of sandbox, permissions; recall end to end (hold, customers, early close refused, bulk disposition, release and reject, CSV, duplicate recall refused, already-held batch untouched, roles); expiry worklist, sweep and minimum shelf life; material return (cost, stock, over-return, role, after completion) and the books; rework (cost, quarantine, expiry kept, loss in unit cost, cancel, recall reaches the output). `tests/demo/test_control_centre.py` runs the same flows black-box against the demo stack.

## 8. Defect found on the way

The demo date re-anchor (`demo_ops reset`) moved every date column in one statement and failed on a unique daily series (campaign metrics) as soon as the shift was a few days. It now parks and restores such tables in two steps (`ISSUE_REGISTER.md` D-018).
