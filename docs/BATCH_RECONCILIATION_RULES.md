# Batch quantity reconciliation rules

Every batch page carries a reconciliation panel. Each line compares **two figures that were recorded independently**. If they differ, the page shows a red banner with the difference. **Nothing is ever adjusted, hidden or "fixed" by the page** (test: `test_reconciliation_balances_and_a_mismatch_is_reported_never_corrected`).

## Quantity vocabulary

| Term | Meaning | Source |
|---|---|---|
| **Physical** | units in the building for this batch, whatever their quality status | `stock_balances.qty_physical` |
| **Reserved** | physical units promised to an order or run, not yet issued/shipped | `qty_reserved` |
| **Blocked** | physical units that may not be used or sold: QUARANTINE, HOLD, REJECTED | `qty_blocked` |
| **Available** | physical − reserved − blocked | `qty_available` |
| **Received / Produced** | units that entered | ledger `RECEIPT`, `PRODUCTION_OUTPUT` |
| **Consumed** | issued to production orders | ledger `PRODUCTION_ISSUE`, confirmed by `production_consumptions` |
| **Dispatched** | left on a shipment | ledger `DISPATCH`, confirmed by `shipment_lines` |
| **Delivered** | the dispatched part whose shipment has a delivery date | `shipments.delivered_at` |
| **Returned to stock** | customer returns received back | ledger `RETURN`; a rejected return stays blocked, a restocked one is released by QC |
| **Returned to supplier** | purchase returns | ledger `ISSUE` |

## The checks

| # | Check | Applies to | Expected | Recorded |
|---|---|---|---|---|
| 1 | Stock ledger equals physical stock | every batch | Σ all ledger `qty_delta` | Σ `qty_physical` |
| 2 | Consumption records equal stock issues | any batch that was consumed | `Σ production_consumptions.qty` | `−Σ` ledger `PRODUCTION_ISSUE` |
| 3 | Produced quantity equals production output | batches made by an order | `production_orders.actual_qty` | ledger `PRODUCTION_OUTPUT` |
| 4 | Shipment lines equal dispatch movements | any batch that shipped | `Σ shipment_lines.qty` | `−Σ` ledger `DISPATCH` |
| 5 | Received returns equal return movements | any batch with returns | return lines of **decided** returns | ledger `RETURN` |
| 6 | Movement equation equals physical | every batch | received + produced + returned-to-stock + adjustments + net transfers − consumed − dispatched − returned to supplier | Σ `qty_physical` |

Check 5 counts every *decided* return, restocked or rejected, because the stock movement happens when the goods are received; the decision only says where they go next. (This was verified against all 116 demo batches, and corrected once during development when a draft of the rule counted only RESTOCK.) Check 6 exists so that any movement type not in the list shows up as a discrepancy instead of passing silently.

## Units

Quantities are in the batch's own unit of measure and are labelled with it. They are **never added across materials** (kg of surfactant + litres of oil + jars). Where a product page shows planned versus consumed per material it does so per material, with that material's unit. Unit conversions (`unit_conversions`) are not applied on this screen: each batch is shown in the unit it is stocked in.

## Finished goods, in words

```
produced
  − dispatched                     (shipment lines)
  + returned to stock              (received customer returns)
  ± adjustments                    (stock counts, corrections)
  ± transfers (net zero)
  = physical, of which: reserved, blocked (QC), available
```

Rejected output is not subtracted from "produced": a rejected batch stays physically in the building as **blocked** stock until it is written off by an adjustment, so it is reported as blocked, never as sellable.

## Quality status and eligibility

A stock count or manual adjustment on a batch that is not RELEASED now adds to **blocked** as well as physical, so unreleased stock can never become available through an adjustment. (A defect in the original `adjust()` allowed it; fixed with a regression test, `test_adjustment_on_unreleased_stock_stays_blocked`.)

## Known gaps

* Production has no "return material to stock" movement, so material returns are always 0.
* Wastage is recorded per order (`wastage_records`) but is not a ledger movement; it appears on the product's production tab, not in a batch's equation.
