"""Read-only financial reports computed from the ledger and documents: ageing, statements, GST summaries, P&L, balance sheet, product costing/margin.
Nothing here writes. Money is Decimal end-to-end; every figure can be traced to journal lines or documents."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.core.errors import NotFound

BUCKETS = [("current", None, 0), ("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, None)]
D = lambda v: Decimal(str(v or 0))

# GST state codes (first two digits of a GSTIN). Free-text states are matched by name or the usual 2-letter abbreviation.
STATE_CODES = {"jammu and kashmir": "01", "jk": "01", "himachal pradesh": "02", "hp": "02", "punjab": "03", "pb": "03", "chandigarh": "04", "ch": "04", "uttarakhand": "05", "uk": "05",
               "haryana": "06", "hr": "06", "delhi": "07", "dl": "07", "rajasthan": "08", "rj": "08", "uttar pradesh": "09", "up": "09", "bihar": "10", "br": "10", "sikkim": "11", "sk": "11",
               "arunachal pradesh": "12", "nagaland": "13", "manipur": "14", "mizoram": "15", "tripura": "16", "meghalaya": "17", "assam": "18", "as": "18", "west bengal": "19", "wb": "19",
               "jharkhand": "20", "jh": "20", "odisha": "21", "od": "21", "chhattisgarh": "22", "cg": "22", "madhya pradesh": "23", "mp": "23", "gujarat": "24", "gj": "24",
               "daman and diu": "25", "dadra and nagar haveli": "26", "maharashtra": "27", "mh": "27", "karnataka": "29", "ka": "29", "goa": "30", "ga": "30", "kerala": "32", "kl": "32",
               "tamil nadu": "33", "tn": "33", "puducherry": "34", "telangana": "36", "ts": "36", "andhra pradesh": "37", "ap": "37", "ladakh": "38"}


def state_code(gstin: str | None, state: str | None) -> str | None:
    if gstin and len(gstin) >= 2 and gstin[:2].isdigit():
        return gstin[:2]
    return STATE_CODES.get((state or "").strip().lower())


def _bucket(days_overdue: int) -> str:
    for name, lo, hi in BUCKETS:
        if (lo is None or days_overdue >= lo) and (hi is None or days_overdue <= hi):
            return name
    return "90+"


def ageing(db: Session, kind: str, as_of: date) -> dict:
    """kind = receivables | payables. Due date = invoice date + the party's credit/payment terms (0 when none are set). Overdue days are relative to `as_of`."""
    if kind == "receivables":
        sql = """SELECT i.id, i.number, i.customer_id AS party_id, c.name AS party, i.invoice_date, i.total - i.amount_paid - i.credited_amount AS outstanding,
                        i.invoice_date + COALESCE(c.credit_days, 0) AS due FROM sales_invoices i JOIN customers c ON c.id = i.customer_id
                  WHERE i.total - i.amount_paid - i.credited_amount > 0 AND i.invoice_date <= :d ORDER BY i.invoice_date, i.id"""
    else:
        sql = """SELECT i.id, i.number, i.supplier_id AS party_id, s.name AS party, i.invoice_date, i.total - i.amount_paid - i.credited_amount AS outstanding,
                        i.invoice_date + COALESCE(s.payment_terms_days, 0) AS due FROM supplier_invoices i JOIN suppliers s ON s.id = i.supplier_id
                  WHERE i.total - i.amount_paid - i.credited_amount > 0 AND i.invoice_date <= :d ORDER BY i.invoice_date, i.id"""
    parties: dict[int, dict] = {}
    totals = {b[0]: Decimal(0) for b in BUCKETS}
    for r in db.execute(text(sql), {"d": as_of}).mappings():
        overdue = (as_of - r["due"]).days
        b = _bucket(max(overdue, 0) if overdue > 0 else 0)
        p = parties.setdefault(r["party_id"], {"party_id": r["party_id"], "party": r["party"], "buckets": {x[0]: Decimal(0) for x in BUCKETS}, "total": Decimal(0), "invoices": []})
        p["buckets"][b] += r["outstanding"]
        p["total"] += r["outstanding"]
        totals[b] += r["outstanding"]
        p["invoices"].append({"invoice_id": r["id"], "number": r["number"], "invoice_date": r["invoice_date"], "due_date": r["due"], "days_overdue": max(overdue, 0), "outstanding": str(r["outstanding"]), "bucket": b})
    out = []
    for p in parties.values():
        out.append({**p, "buckets": {k: str(v) for k, v in p["buckets"].items()}, "total": str(p["total"])})
    return {"kind": kind, "as_of": as_of, "parties": out, "totals": {k: str(v) for k, v in totals.items()}, "grand_total": str(sum(totals.values(), Decimal(0)))}


def statement(db: Session, party_type: str, party_id: int, start: date, end: date) -> dict:
    """Chronological statement with a running balance (amount the party owes us / we owe them). Opening balance = everything before `start`."""
    if party_type == "customer":
        if not db.execute(text("SELECT 1 FROM customers WHERE id = :i"), {"i": party_id}).first():
            raise NotFound("Customer")
        ev = """SELECT i.invoice_date AS d, 'INVOICE' AS kind, i.number AS ref, i.total AS debit, 0::numeric AS credit FROM sales_invoices i WHERE i.customer_id = :p
                UNION ALL SELECT business_date(cn.created_at), 'CREDIT_NOTE', cn.number, 0, cn.total FROM credit_notes cn JOIN sales_invoices i ON i.id = cn.invoice_id WHERE i.customer_id = :p
                UNION ALL SELECT p.payment_date, 'PAYMENT', p.number, 0, p.amount FROM payments p WHERE p.party_type = 'customer' AND p.party_id = :p
                UNION ALL SELECT business_date(ms.posted_at), 'SETTLEMENT_DEDUCTION', ms.reference, 0, ms.commission + ms.shipping_fees + ms.ad_fees + ms.other_deductions
                  FROM marketplace_settlements ms JOIN marketplace_accounts a ON a.id = ms.account_id WHERE a.customer_id = :p AND ms.posted_at IS NOT NULL"""
    else:
        if not db.execute(text("SELECT 1 FROM suppliers WHERE id = :i"), {"i": party_id}).first():
            raise NotFound("Supplier")
        ev = """SELECT i.invoice_date AS d, 'INVOICE' AS kind, i.number AS ref, 0::numeric AS debit, i.total AS credit FROM supplier_invoices i WHERE i.supplier_id = :p
                UNION ALL SELECT business_date(dn.created_at), 'DEBIT_NOTE', dn.number, dn.total, 0 FROM debit_notes dn JOIN supplier_invoices i ON i.id = dn.supplier_invoice_id WHERE i.supplier_id = :p
                UNION ALL SELECT p.payment_date, 'PAYMENT', p.number, p.amount, 0 FROM payments p WHERE p.party_type = 'supplier' AND p.party_id = :p"""
    rows = db.execute(text(f"SELECT * FROM ({ev}) x ORDER BY d, kind, ref"), {"p": party_id}).mappings().all()
    sign = 1 if party_type == "customer" else -1                     # customer: debit increases what they owe; supplier: credit increases what we owe
    bal = Decimal(0)
    opening = Decimal(0)
    lines = []
    for r in rows:
        delta = sign * (D(r["debit"]) - D(r["credit"]))
        if r["d"] < start:
            opening += delta
            bal = opening
        elif r["d"] <= end:
            bal += delta
            lines.append({"date": r["d"], "type": r["kind"], "reference": r["ref"], "debit": str(D(r["debit"])), "credit": str(D(r["credit"])), "balance": str(bal)})
    return {"party_type": party_type, "party_id": party_id, "period": {"start": start, "end": end}, "opening_balance": str(opening), "lines": lines, "closing_balance": str(bal),
            "meaning": "amount owed to us" if party_type == "customer" else "amount we owe"}


def _tax_split(tax: Decimal, intra: bool) -> dict:
    return {"cgst": tax / 2, "sgst": tax / 2, "igst": Decimal(0)} if intra else {"cgst": Decimal(0), "sgst": Decimal(0), "igst": tax}


def gst_summary(db: Session, start: date, end: date) -> dict:
    """GSTR-1 / GSTR-3B style summaries from documents. Place of supply = customer GSTIN state, else customer state, else assumed intra-state (counted and flagged).
    These are working summaries for the accountant, NOT a filing: statutory validation with Mintree's tax advisor is still required (PRD 10.5)."""
    company_code = str(get_setting(db, "company.state_code") or "27")
    rows = db.execute(text("""
        SELECT i.id AS invoice_id, i.number, i.invoice_date, c.id AS customer_id, c.name, c.gstin, c.state, it.hsn_code, soi.tax_rate,
               sum(sl.qty * soi.unit_price) AS taxable, sum(sl.qty) AS qty
          FROM sales_invoices i JOIN customers c ON c.id = i.customer_id JOIN shipments sh ON sh.id = i.shipment_id OR (i.shipment_id IS NULL AND sh.so_id = i.so_id)
          JOIN shipment_lines sl ON sl.shipment_id = sh.id JOIN sales_order_items soi ON soi.id = sl.so_item_id JOIN items it ON it.id = sl.item_id
         WHERE i.invoice_date BETWEEN :s AND :e GROUP BY i.id, c.id, it.hsn_code, soi.tax_rate ORDER BY i.id"""), {"s": start, "e": end}).mappings().all()
    by_rate: dict[str, dict] = {}
    hsn: dict[tuple, dict] = {}
    b2b = b2c = Decimal(0)
    unknown_state = 0
    zero = lambda: {"taxable": Decimal(0), "cgst": Decimal(0), "sgst": Decimal(0), "igst": Decimal(0)}
    for r in rows:
        code = state_code(r["gstin"], r["state"])
        if code is None:
            unknown_state += 1
        intra = code is None or code == company_code
        taxable = D(r["taxable"]).quantize(Decimal("0.01"))
        tax = (taxable * D(r["tax_rate"]) / 100).quantize(Decimal("0.01"))
        key = str(D(r["tax_rate"]).normalize())
        b = by_rate.setdefault(key, zero())
        b["taxable"] += taxable
        for k, v in _tax_split(tax, intra).items():
            b[k] += v
        h = hsn.setdefault((r["hsn_code"] or "UNSPECIFIED", key), {"hsn": r["hsn_code"] or "UNSPECIFIED", "rate": key, "qty": Decimal(0), **zero()})
        h["qty"] += D(r["qty"])
        h["taxable"] += taxable
        for k, v in _tax_split(tax, intra).items():
            h[k] += v
        if r["gstin"]:
            b2b += taxable
        else:
            b2c += taxable
    cn = db.execute(text("""SELECT COALESCE(sum(cn.subtotal),0) sub, COALESCE(sum(cn.tax_total),0) tax, count(*) n FROM credit_notes cn WHERE business_date(cn.created_at) BETWEEN :s AND :e"""), {"s": start, "e": end}).mappings().one()
    dn = db.execute(text("SELECT COALESCE(sum(subtotal),0) sub, COALESCE(sum(tax_total),0) tax, count(*) n FROM debit_notes WHERE business_date(created_at) BETWEEN :s AND :e"), {"s": start, "e": end}).mappings().one()
    inward = db.execute(text("SELECT COALESCE(sum(subtotal),0) sub, COALESCE(sum(tax_total),0) tax, count(*) n FROM supplier_invoices WHERE invoice_date BETWEEN :s AND :e"), {"s": start, "e": end}).mappings().one()
    out_tax = sum((b["cgst"] + b["sgst"] + b["igst"] for b in by_rate.values()), Decimal(0))
    fmt = lambda d: {k: (str(v) if isinstance(v, Decimal) else v) for k, v in d.items()}
    net_payable = out_tax - D(cn["tax"]) - (D(inward["tax"]) - D(dn["tax"]))
    return {"period": {"start": start, "end": end}, "company_state_code": company_code, "unknown_place_of_supply_lines": unknown_state,
            "outward_supplies_by_rate": {k: fmt(v) for k, v in sorted(by_rate.items(), key=lambda kv: Decimal(kv[0]))},
            "hsn_summary": [fmt(v) for v in sorted(hsn.values(), key=lambda x: (x["hsn"], Decimal(x["rate"])))],
            "b2b_taxable_value": str(b2b), "b2c_taxable_value": str(b2c),
            "credit_notes": {"count": cn["n"], "taxable": str(cn["sub"]), "tax": str(cn["tax"]), "note": "rate split not stored on credit notes"},
            "gstr3b": {"output_tax": str(out_tax), "less_credit_notes_tax": str(cn["tax"]), "input_tax_credit": str(D(inward["tax"]) - D(dn["tax"])),
                       "supplier_invoices": inward["n"], "supplier_debit_notes_tax": str(dn["tax"]), "net_tax_payable": str(net_payable)},
            "disclaimer": "Working summary from system records; not a GST return. E-invoice / e-way bill generation needs an authorised provider and is not connected."}


def _balances(db: Session, start: date | None, end: date) -> list[dict]:
    return [dict(r) for r in db.execute(text("""
        SELECT a.code, a.name, a.type, COALESCE(sum(l.debit),0) AS debit, COALESCE(sum(l.credit),0) AS credit
          FROM accounts a JOIN journal_lines l ON l.account_id = a.id JOIN journal_entries e ON e.id = l.entry_id
         WHERE e.entry_date <= :e AND (CAST(:s AS date) IS NULL OR e.entry_date >= :s) GROUP BY a.id ORDER BY a.code"""), {"s": start, "e": end}).mappings()]


def profit_and_loss(db: Session, start: date, end: date) -> dict:
    rows = _balances(db, start, end)
    rev = [(r, D(r["credit"]) - D(r["debit"])) for r in rows if r["type"] == "REVENUE"]
    exp = [(r, D(r["debit"]) - D(r["credit"])) for r in rows if r["type"] == "EXPENSE"]
    line = lambda r, v: {"code": r["code"], "name": r["name"], "amount": str(v)}
    revenue = sum((v for _, v in rev), Decimal(0))
    cogs = sum((v for r, v in exp if r["code"] == "5000"), Decimal(0))
    expenses = sum((v for _, v in exp), Decimal(0))
    return {"period": {"start": start, "end": end}, "revenue": [line(r, v) for r, v in rev], "total_revenue": str(revenue),
            "cost_of_goods_sold": str(cogs), "gross_profit": str(revenue - cogs), "gross_margin_pct": str((100 * (revenue - cogs) / revenue).quantize(Decimal("0.01"))) if revenue else None,
            "expenses": [line(r, v) for r, v in exp], "total_expenses": str(expenses), "net_profit": str(revenue - expenses)}


def balance_sheet(db: Session, as_of: date) -> dict:
    rows = _balances(db, None, as_of)
    side = lambda t, sign: [{"code": r["code"], "name": r["name"], "amount": str(sign * (D(r["debit"]) - D(r["credit"])))} for r in rows if r["type"] == t and (D(r["debit"]) or D(r["credit"]))]
    total = lambda lst: sum((D(x["amount"]) for x in lst), Decimal(0))
    assets, liabs, equity = side("ASSET", 1), side("LIABILITY", -1), side("EQUITY", -1)
    earnings = sum((D(r["credit"]) - D(r["debit"]) for r in rows if r["type"] == "REVENUE"), Decimal(0)) - sum((D(r["debit"]) - D(r["credit"]) for r in rows if r["type"] == "EXPENSE"), Decimal(0))
    ta, tl, te = total(assets), total(liabs), total(equity) + earnings
    return {"as_of": as_of, "assets": assets, "total_assets": str(ta), "liabilities": liabs, "total_liabilities": str(tl), "equity": equity,
            "retained_earnings_to_date": str(earnings), "total_equity": str(te), "balanced": ta == tl + te}


def product_costing(db: Session, start: date, end: date) -> dict:
    """Per finished product: last production unit cost, current moving-average cost, units sold, revenue, COGS (from shipment lines at issue cost), margin.
    Overhead is NOT posted in the ledger; `allocated_overhead` applies the costing.overhead_pct setting to COGS purely as a reporting view."""
    ovh = D(get_setting(db, "costing.overhead_pct") or 0)
    rows = db.execute(text("""
        SELECT it.id, it.item_code, it.name,
               (SELECT po.unit_cost FROM production_orders po WHERE po.output_item_id = it.id AND po.status = 'COMPLETED' ORDER BY po.completed_at DESC LIMIT 1) AS last_production_cost,
               (SELECT po.yield_pct FROM production_orders po WHERE po.output_item_id = it.id AND po.status = 'COMPLETED' ORDER BY po.completed_at DESC LIMIT 1) AS last_yield_pct,
               CASE WHEN v.qty > 0 THEN v.value / v.qty END AS current_avg_cost,
               COALESCE(s.qty,0) AS units_sold, COALESCE(s.revenue,0) AS revenue, COALESCE(s.cogs,0) AS cogs
          FROM items it LEFT JOIN item_valuation v ON v.item_id = it.id
          LEFT JOIN (SELECT sl.item_id, sum(sl.qty) qty, sum(sl.qty * soi.unit_price) revenue, sum(sl.qty * sl.unit_cost) cogs
                       FROM shipment_lines sl JOIN shipments sh ON sh.id = sl.shipment_id JOIN sales_order_items soi ON soi.id = sl.so_item_id
                       JOIN sales_invoices i ON i.shipment_id = sh.id OR (i.shipment_id IS NULL AND i.so_id = sh.so_id) WHERE i.invoice_date BETWEEN :s AND :e GROUP BY sl.item_id) s ON s.item_id = it.id
         WHERE it.kind = 'FG' AND (s.qty IS NOT NULL OR v.qty > 0 OR EXISTS (SELECT 1 FROM production_orders po WHERE po.output_item_id = it.id)) ORDER BY it.item_code"""), {"s": start, "e": end}).mappings().all()
    waste = db.execute(text("""SELECT po.output_item_id AS item_id, sum(w.qty) AS qty, sum(w.qty * COALESCE(po.unit_cost, 0)) AS value_at_cost
                                 FROM wastage_records w JOIN production_orders po ON po.id = w.order_id GROUP BY po.output_item_id""")).mappings().all()
    wmap = {w["item_id"]: w for w in waste}
    out = []
    for r in rows:
        rev, cogs = D(r["revenue"]), D(r["cogs"])
        alloc = (cogs * ovh / 100).quantize(Decimal("0.01"))
        out.append({"item_id": r["id"], "item_code": r["item_code"], "name": r["name"], "last_production_unit_cost": None if r["last_production_cost"] is None else str(r["last_production_cost"]),
                    "last_yield_pct": None if r["last_yield_pct"] is None else str(r["last_yield_pct"]), "current_avg_cost": None if r["current_avg_cost"] is None else str(D(r["current_avg_cost"]).quantize(Decimal("0.0001"))),
                    "units_sold": str(r["units_sold"]), "revenue_ex_tax": str(rev), "cogs": str(cogs), "gross_margin": str(rev - cogs),
                    "gross_margin_pct": str((100 * (rev - cogs) / rev).quantize(Decimal("0.01"))) if rev else None,
                    "allocated_overhead": str(alloc), "margin_after_overhead": str(rev - cogs - alloc),
                    "wastage_qty": str(D(wmap[r["id"]]["qty"])) if r["id"] in wmap else "0", "wastage_value_at_cost": str(D(wmap[r["id"]]["value_at_cost"]).quantize(Decimal("0.01"))) if r["id"] in wmap else "0.00"})
    return {"period": {"start": start, "end": end}, "overhead_pct_applied": str(ovh), "products": out,
            "notes": "COGS is the moving-average cost issued at dispatch (posted to 5000). Overhead/labour are not captured in the ledger; the allocation is a reporting view."}
