# Customer invoice workflow

From a customer order to a paid invoice, including the draft, validate and post steps. Every state below exists in the database; nothing is displayed that is not stored.

## 1. How invoicing works here, and what a draft is

In this system a customer invoice is born **when goods ship**: dispatch posts, in one transaction, the stock-out, the shipment and its lines, the cost-of-goods journal, the revenue / receivable / output-GST journal, a gapless invoice number (`SI/<fiscal year>/<6 digits>`, locked counter) and the invoice row. That is unchanged.

A **draft invoice** is the reviewed step before that. It lists the eligible lines (ordered quantity not yet shipped), previews the exact taxable value and GST that posting will bill (`dispatch.line_amounts`, the same arithmetic dispatch uses, including the exact-remainder rule on the final shipment of a line), and lets someone validate it. **A draft records nothing financial.** Posting a draft runs the existing dispatch posting for exactly those lines, so the invoice number, the journal, the stock movements, the shipment and the audit trail are the ones an ordinary dispatch produces.

> Design note. The first idea was to draft invoices against "shipped but not yet invoiced" quantities. That set is always empty here, because dispatch invoices everything it ships, and the integrity checks (`check_integrity`) require exactly one invoice per shipment. Rather than weaken that control, a draft sits *before* dispatch and posting it performs the dispatch. If the business later wants invoices separate from shipments, that is a deliberate accounting change, not a UI change.

## 2. States

```
(order CONFIRMED / ALLOCATED / PACKED / PARTIALLY_DISPATCHED)
        |  Create invoice draft
        v
     DRAFT ---- Validate ----> VALIDATED ---- Post ----> POSTED  (a real invoice exists)
        \                          /
         \----- Cancel ----------/--> CANCELLED  (nothing was ever posted)
```

| Event | Financial effect |
|---|---|
| Draft created | none: no journal entry, no invoice number, no revenue, no receivable |
| Validated | none |
| Cancelled | none |
| **Posted** | stock out, shipment, cost of goods, revenue, receivable and output GST, gapless invoice number, audit, `dispatch.order.dispatched` event |
| Payment recorded and allocated | cash/bank vs receivable; invoice becomes PARTIAL then PAID; ageing and receivables follow |
| Return accepted | credit note and reversal through the existing returns workflow |

## 3. Rules enforced (server side)

* The **eligible quantity** of a line is `ordered - shipped`. A draft can never ask for more (`409 exceeds_remaining`); posting re-checks against the locked order.
* **One open draft per order** (unique partial index): no accidental duplicates. Cancel or post it before drafting again.
* **Validate** blocks posting when: the order is not yet PACKED (or partly dispatched); a line's quantity no longer fits; the amounts changed because another shipment posted meanwhile; the buyer is GST-registered but has no billing address. It only **warns** when an unregistered buyer has no address, or when neither GSTIN nor state is known (the invoice is then treated as intra-state, CGST + SGST). Errors return `409 draft_not_valid` with the full list; the draft stays DRAFT.
* **Post** needs a validated draft and runs under an idempotency key: a retry replays the same result; a second post of a posted draft is `409 already_posted`; a cancelled draft cannot be posted. The unique `shipment_id` on invoices makes a duplicate invoice impossible even if everything else failed.
* **Money**: `NUMERIC(18,2)` throughout, GST per line rounded half-up once, totals are the sum of rounded lines, `total = subtotal + tax` is a database check constraint. Invoice numbers come only from the backend counter.
* **Partial invoicing** is partial shipping: draft some lines or part quantities, post, then draft the remainder. The invoices add up to the order total exactly (tested: 295 + 590 = 885).
* **Permissions**: drafting needs `accounts.sales_invoice:draft` (dispatch managers and accounts); posting needs `accounts.sales_invoice:post` **and** `dispatch.order:dispatch`, because posting ships the goods. Sales operators and production operators can do neither; accounts alone cannot post. Existing databases get the two new permissions added to the roles that carry them by default (additive only, `seed.UPGRADE_GRANTS`).

## 4. Screens

* **Dispatch -> Sales orders**: row action **Create invoice draft** (order is confirmed or later).
* **Dispatch -> Invoice drafts**: list with status filter; **Validate**, **Post invoice** (confirm dialog), **Invoice PDF** (once posted), **Cancel draft**.
* **Dispatch -> Sales invoices**: the posted invoice, **PDF**, **Receive payment** (allocated to that invoice).
* **Demo Control Centre -> Accounts**: Create draft invoice, Post invoice, Generate invoice PDF, Record and allocate payment.

## 5. PDF

`GET /api/dispatch/invoices/{id}/pdf` produces the tax invoice on the Mintree brand layout (header, company GSTIN, bill-to / ship-to, HSN, taxable value, CGST+SGST or IGST, total, QR link to the record). The generator refuses to produce a PDF whose lines do not reconcile with the stored total. The PDF says that **e-invoice IRN and e-way bill are not attached**: no GST e-invoice integration exists and none is claimed.

## 6. API

| Method and path | Purpose |
|---|---|
| `GET /api/dispatch/orders/{id}/invoice-eligibility` | lines still to ship and bill |
| `POST /api/dispatch/orders/{id}/invoice-drafts` | create a draft (`lines` optional; omit for everything remaining) |
| `GET /api/dispatch/invoice-drafts[?status=]`, `GET .../{id}` | list / read |
| `POST /api/dispatch/invoice-drafts/{id}/validate` | validate (`409` with the errors if not ready) |
| `POST /api/dispatch/invoice-drafts/{id}/post` | post (`Idempotency-Key` required) |
| `POST /api/dispatch/invoice-drafts/{id}/cancel` | cancel |

## 7. Tests

`backend/tests/test_invoice_drafts.py` (10 tests): a draft changes nothing in the ledger and equals what posting bills; the posted invoice has a gapless number, a balanced journal and revenue only after posting; a replayed post returns the same invoice and a second post is refused; over-quantity refused; partial invoicing adds up to the order total; validate-before-post and packed-order rules; registered vs unregistered buyer; cancel leaves no trace; payment settles the invoice; who may draft and post; anonymous refused. `tests/demo/test_control_centre.py` and `tests/demo/ui.mjs` repeat the flow end to end against the demo stack, including the PDF.

## 8. Not built (stated plainly)

* **Discounts and extra charges on the invoice** (freight, packing). The PDF derives its lines from the shipment so that it always reconciles with the stored total; allowing adjustments needs an invoice-line table and a PDF change. Freight is recorded on the shipment for margin and is not billed.
* **Draft PDF preview** (a watermarked draft). The screen preview is the totals and lines; the PDF exists for posted invoices.
* **Invoice cancellation / reversal** of a posted invoice. The supported correction is a return with a credit note.
* **GST e-invoice (IRN) and e-way bill**: not integrated, not claimed.
* **Credit note PDF** and **line-level quantity editing inside the draft screen** (the API accepts `lines`; the screen drafts all remaining lines).
