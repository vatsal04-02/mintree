# Traceability API

Base path `/api/traceability`. All endpoints are **GET** except impact analysis (**POST**, because it writes one audit row). Session cookie + CSRF header, like the rest of the API. Paging: `page` ≥ 1, `page_size` 1–100 (default 25); `page_size` over 100 is rejected with 422. Quantities are decimal **strings** with at most three places; dates are ISO 8601.

Errors: `403` missing permission, `404` unknown batch or product, `422` bad parameter.

## Batches (`inventory.batch:read`)

| Endpoint | Returns |
|---|---|
| `GET /batches?q&item_id&kind=RM\|PM\|BULK\|FG&qc_status&page&page_size` | `{items[{id,batch_no,item_code,item_name,kind,uom,qc_status,mfg_date,expiry_date,supplier_lot_no,physical,available}], total, page, page_size}` |
| `GET /batches/{id}` | `{batch, source_receipt, production_order, locations[], reconciliation, inspections[]}` (below) |
| `GET /batches/{id}/graph?direction=both\|backward\|forward&max_depth=1..6` | `{focus, direction, nodes[], edges[], truncated, limits}` |
| `GET /batches/{id}/movements?page&page_size` | paged stock-ledger rows, newest first |
| `GET /batches/{id}/history` | audit rows for the batch, its inspections and impact analyses |
| `POST /batches/{id}/impact-analysis?max_depth` | requires `inventory.batch:qc_release` too; see `BATCH_IMPACT_ANALYSIS.md` |

### Detail

```jsonc
{ "batch": { "id", "batch_no", "item_id", "item_code", "item_name", "kind", "kind_label", "uom", "supplier_lot_no", "supplier_name"|"Restricted", "mfg_date", "expiry_date",
             "received_at", "qc_status", "qc_decided_at", "qc_decided_by", "source_type", "business_status" },
  "source_receipt": { "id", "number", "received_at", "po_id", "po_number", "supplier_name", "delivery_note_no" } | null,
  "production_order": { "id", "number", "status", "planned_qty", "actual_qty", "yield_pct", "unit_cost", "formulation", "formulation_version", "bom_version" } | null,
  "locations": [{ "warehouse", "warehouse_name", "location", "physical", "reserved", "blocked", "available" }],
  "reconciliation": { "unit", "quantities": { "received","produced","returned_to_stock","consumed_in_production","dispatched","returned_to_supplier","adjustments_net","transfers_net",
                                              "physical","reserved","blocked","available","delivered","return_requested" },
                      "equation": {...}, "checks": [{ "name","expected","actual","difference","ok","note" }], "balanced": true, "warnings": [], "definitions": {...} },
  "inspections": [{ "id","number","stage","status","decision","decision_reason","decided_at","decided_by","failed_results","retested","results":[{ "test","value","passed","observation","tested_at","tested_by" }] }] }
```

### Graph

```jsonc
{ "nodes": [{ "id": "batch:17", "type": "batch|supplier|purchase_order|goods_receipt|opening_stock|production_order|formulation|bom|qc|warehouse|reservation|shipment|sales_order|customer|return",
              "ref": "B260000017", "label": "…", "kind": "FG", "status": "RELEASED", "date": "…", "unit": "PCS", "href": "/inventory/batches/17/traceability", "detail": { … } }],
  "edges": [{ "source": "batch:1", "target": "order:4", "qty": "2", "unit": "KG", "date": null, "label": "consumed|produced|received into|delivered|dispatched|fulfils|delivered to|for|returned|reserved|stored|inspected|recipe|loaded" }],
  "truncated": false, "limits": { "max_depth": 6, "max_nodes": 300 } }
```
Node `id`s are stable (`<type>:<database id>`). `ref` is always text. Edge `qty` is the quantity that moved along that edge in the edge's unit.

## Products (`inventory.batch:read`)

| Endpoint | Returns |
|---|---|
| `GET /products?q&kind&page&page_size` | product list with batch count and available stock (default entry list: finished goods first) |
| `GET /products/{id}` | `{product, recipe, stock{physical,reserved,blocked,available}, metrics{…}, definitions{…}}` |
| `GET /products/{id}/formulations` | BOMs (with orders using each) and formulation versions |
| `GET /products/{id}/materials` | per material: planned, issued, consumed, variance and the **lots actually consumed** with supplier and lot |
| `GET /products/{id}/production` | paged orders: planned, actual, yield, wastage, unit cost, batch, recipe |
| `GET /products/{id}/batches` | paged batches with physical, available, blocked, dispatched |
| `GET /products/{id}/quality` | paged inspections across the product's batches |
| `GET /products/{id}/sales` | paged customers: orders, shipments, batches, shipped, delivered, last shipped |
| `GET /products/{id}/history` | audit rows for the product and its batches |

Metric definitions are returned with the data (`definitions`) so the UI never has to guess what "dispatched" or "QC rejection rate" means.

## Conventions

* **Masking.** `supplier_name` and customer names are the text `Restricted` unless the caller holds `masters.supplier:read` / `masters.customer:read`.
* **No N+1.** Statement count is constant per request (8–24 on the demo database), measured with a SQLAlchemy event counter.
* **Read-only.** Only `impact-analysis` writes (one `audit_logs` row).
