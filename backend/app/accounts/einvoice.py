"""GST e-invoice (IRN) and e-way bill, behind a provider adapter.

WHAT IS REAL HERE. The invoice payload is built to the e-invoice schema (INV-01, v1.1) from the stored invoice and its shipment lines (the same stored taxable
and tax amounts the books use, so the payload always agrees with the ledger); it is validated before anything is sent (GSTIN format and its check character,
state codes, HSN, PIN, tax split, totals); the IRN is the 64-character SHA-256 of seller GSTIN + financial year + document type + document number, which is how
the Invoice Registration Portal defines it; results are stored per invoice, are idempotent (generating again returns the same IRN, never a second one) and an
IRN is never reused; cancellation follows the portal's 24-hour rule and is refused once an e-way bill exists.

WHAT IS NOT. Registration with the government portal needs an account with a GST Suvidha Provider (or the NIC API) and its credentials; none exists here. The only
provider shipped is the SANDBOX one: it computes the real IRN and a clearly marked placeholder acknowledgement and QR, so the whole flow can be built, tested and
shown, and every record, screen and PDF says SANDBOX. LIVE mode refuses to run until a provider adapter is written for the chosen GSP and configured; nothing here
ever presents a sandbox result as a government registration.

Mode comes from MINTREE_EINVOICE_MODE: off | sandbox | live. Production refuses to start in sandbox mode."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts.reports import state_code
from app.approvals.service import get_setting
from app.audit import service as audit
from app.core import activity
from app.core.clock import business_today
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentUser, require
from app.core.errors import BusinessConflict, NotFound
from app.core.uow import run_tx
from app.tier1_models import EInvoice

router = APIRouter(prefix="/accounts/einvoices", tags=["einvoice"])
DB = Depends(get_db, scope="function")
GSTIN_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PIN_RE = re.compile(r"\b[1-9][0-9]{5}\b")
EWB_THRESHOLD = Decimal("50000")
CANCEL_WINDOW = timedelta(hours=24)
CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


# ------------------------------------------------------------------------------------------------------------------------------- GSTIN
def gstin_check_char(first14: str) -> str:
    """The 15th character of a GSTIN (the portal's mod-36 check)."""
    total = 0
    for i, ch in enumerate(first14):
        v = CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += v // 36 + v % 36
    return CHARS[(36 - total % 36) % 36]


def valid_gstin(g: str | None) -> bool:
    g = (g or "").strip().upper()
    return bool(GSTIN_RE.match(g)) and gstin_check_char(g[:14]) == g[14]


def make_gstin(state: str, pan: str, entity: str = "1") -> str:
    """A well-formed GSTIN with a correct check character (demo data and tests)."""
    base = f"{state}{pan}{entity}Z"
    return base + gstin_check_char(base)


# ------------------------------------------------------------------------------------------------------------------------------- providers
@dataclass
class Registration:
    irn: str
    ack_no: str
    ack_date: datetime
    signed_qr: str


class Provider:
    name = "none"
    mode = "SANDBOX"

    def register(self, payload: dict) -> Registration: raise NotImplementedError
    def cancel(self, irn: str, reason: str) -> None: raise NotImplementedError
    def eway(self, irn: str, payload: dict) -> tuple[str, datetime]: raise NotImplementedError


def irn_for(gstin: str, fy: str, doc_type: str, doc_no: str) -> str:
    """The IRP's definition: SHA-256 (hex, 64 characters) of supplier GSTIN, financial year, document type and document number."""
    return hashlib.sha256(f"{gstin}{fy}{doc_type}{doc_no}".encode()).hexdigest()


class SandboxProvider(Provider):
    """Computes the real IRN; acknowledgement number, date and QR are placeholders marked SANDBOX. Registers nothing with any government system."""
    name, mode = "sandbox", "SANDBOX"

    def register(self, payload: dict) -> Registration:
        irn = irn_for(payload["SellerDtls"]["Gstin"], payload["_fy"], payload["DocDtls"]["Typ"], payload["DocDtls"]["No"])
        ack_no = str(int(irn[:12], 16))[:15].rjust(15, "1")
        now = datetime.now(timezone.utc)
        return Registration(irn, ack_no, now, f"SANDBOX-NOT-VALID|IRN:{irn}|ACK:{ack_no}|DOC:{payload['DocDtls']['No']}|VAL:{payload['ValDtls']['TotInvVal']}")

    def cancel(self, irn: str, reason: str) -> None:
        return None

    def eway(self, irn: str, payload: dict) -> tuple[str, datetime]:
        no = str(int(hashlib.sha256(("EWB" + irn).encode()).hexdigest()[:12], 16))[:12].rjust(12, "3")
        days = max(1, -(-int(payload.get("Distance") or 100) // 200))
        return no, datetime.now(timezone.utc) + timedelta(days=days)


class LiveProvider(Provider):
    name, mode = "live", "LIVE"

    def _no(self):
        raise BusinessConflict("einvoice_provider_not_configured", "Live e-invoicing needs an adapter for your GST Suvidha Provider (or the NIC API) and its credentials; none is configured. "
                                                                    "Use sandbox mode to build and test the flow; sandbox results are not government registrations.")

    def register(self, payload): self._no()
    def cancel(self, irn, reason): self._no()
    def eway(self, irn, payload): self._no()


def provider() -> Provider:
    mode = (get_settings().einvoice_mode or "off").lower()
    if mode == "off":
        raise BusinessConflict("einvoice_off", "E-invoicing is switched off for this installation (MINTREE_EINVOICE_MODE)")
    return LiveProvider() if mode == "live" else SandboxProvider()


# ------------------------------------------------------------------------------------------------------------------------------- payload
def fy_label(d) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def _d(v) -> Decimal:
    return Decimal(str(v)).quantize(Decimal("0.01"))


def build(db: Session, invoice_id: int) -> tuple[dict, list[str]]:
    """-> (payload, errors). The payload is what would be sent; `errors` is everything that would make the portal refuse it."""
    inv = db.execute(text("""SELECT i.id, i.number, i.invoice_date, i.subtotal, i.tax_total, i.total, i.customer_id, i.so_id, i.shipment_id FROM sales_invoices i WHERE i.id = :i"""), {"i": invoice_id}).mappings().first()
    if not inv:
        raise NotFound("Invoice")
    errs: list[str] = []
    cust = db.execute(text("SELECT name, gstin, state, billing_address, shipping_address FROM customers WHERE id = :c"), {"c": inv["customer_id"]}).mappings().one()
    company = {k: get_setting(db, f"company.{k}") for k in ("name", "gstin", "address", "state_code")}
    sg = (company["gstin"] or "").strip().upper()
    bg = (cust["gstin"] or "").strip().upper()
    if not valid_gstin(sg):
        errs.append("The company GSTIN is missing or not valid (Administration > Settings > company.gstin)")
    if not bg:
        errs.append(f"{cust['name']} has no GSTIN: e-invoicing applies to sales to GST-registered buyers (B2B)")
    elif not valid_gstin(bg):
        errs.append(f"{cust['name']}'s GSTIN {bg} is not valid")
    sstate = (sg[:2] if valid_gstin(sg) else str(company["state_code"] or "")).rjust(2, "0")[:2]
    bstate = state_code(bg or None, cust["state"]) or ""
    if not re.fullmatch(r"\d{2}", bstate or ""):
        errs.append(f"{cust['name']}'s state could not be determined")
    for who, addr in (("The company", company["address"]), (cust["name"], cust["billing_address"])):
        if not (addr or "").strip():
            errs.append(f"{who} has no address on record")
    spin = PIN_RE.search(company["address"] or "")
    bpin = PIN_RE.search(cust["billing_address"] or "")
    if not spin:
        errs.append("The company address needs a 6-digit PIN code")
    if not bpin:
        errs.append(f"{cust['name']}'s billing address needs a 6-digit PIN code")
    lines = db.execute(text("""
        SELECT soi.line_no, it.name, it.hsn_code, COALESCE(u.code, 'NOS') AS unit, soi.unit_price, soi.tax_rate, sum(sl.qty) AS qty, sum(sl.net_amount) AS net, sum(sl.tax_amount) AS tax
          FROM shipment_lines sl JOIN sales_order_items soi ON soi.id = sl.so_item_id JOIN items it ON it.id = sl.item_id LEFT JOIN units_of_measure u ON u.id = it.uom_id
         WHERE sl.shipment_id = :s GROUP BY soi.line_no, it.name, it.hsn_code, u.code, soi.unit_price, soi.tax_rate ORDER BY soi.line_no"""), {"s": inv["shipment_id"]}).mappings().all() if inv["shipment_id"] else []
    if not lines:
        errs.append("The invoice has no shipment lines to report")
    intra = bool(sstate) and sstate == bstate
    items, a_sum, c_sum, s_sum, i_sum = [], Decimal(0), Decimal(0), Decimal(0), Decimal(0)
    for n, l in enumerate(lines, 1):
        hsn = (l["hsn_code"] or "").strip()
        if not re.fullmatch(r"\d{4,8}", hsn):
            errs.append(f"Line {n} ({l['name']}) needs an HSN code of 4 to 8 digits")
        net, tax = _d(l["net"] or 0), _d(l["tax"] or 0)
        cg = (tax / 2).quantize(Decimal("0.01")) if intra else Decimal(0)
        sg_amt = (tax - cg) if intra else Decimal(0)
        ig = Decimal(0) if intra else tax
        a_sum, c_sum, s_sum, i_sum = a_sum + net, c_sum + cg, s_sum + sg_amt, i_sum + ig
        qty = Decimal(str(l["qty"])).quantize(Decimal("0.001")).normalize()
        items.append({"SlNo": str(n), "PrdDesc": l["name"], "IsServc": "N", "HsnCd": hsn, "Qty": float(qty), "Unit": str(l["unit"])[:8].upper(), "UnitPrice": float(_d(l["unit_price"])),
                      "TotAmt": float(net), "AssAmt": float(net), "GstRt": float(l["tax_rate"]), "IgstAmt": float(ig), "CgstAmt": float(cg), "SgstAmt": float(sg_amt), "TotItemVal": float(net + tax)})
    if _d(a_sum) != _d(inv["subtotal"]) or _d(c_sum + s_sum + i_sum) != _d(inv["tax_total"]):
        errs.append("The invoice lines do not add up to the stored invoice total; the invoice cannot be reported")
    payload = {
        "Version": "1.1", "_fy": fy_label(inv["invoice_date"]),
        "TranDtls": {"TaxSch": "GST", "SupTyp": "B2B", "RegRev": "N", "IgstOnIntra": "N"},
        "DocDtls": {"Typ": "INV", "No": inv["number"], "Dt": inv["invoice_date"].strftime("%d/%m/%Y")},
        "SellerDtls": {"Gstin": sg, "LglNm": company["name"], "Addr1": (company["address"] or "")[:100], "Loc": "", "Pin": spin.group(0) if spin else "", "Stcd": sstate},
        "BuyerDtls": {"Gstin": bg, "LglNm": cust["name"], "Pos": bstate, "Addr1": (cust["billing_address"] or "")[:100], "Loc": "", "Pin": bpin.group(0) if bpin else "", "Stcd": bstate},
        "ItemList": items,
        "ValDtls": {"AssVal": float(a_sum), "CgstVal": float(c_sum), "SgstVal": float(s_sum), "IgstVal": float(i_sum), "TotInvVal": float(_d(inv["total"]))},
    }
    return payload, errs


def _hash(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------------------- operations
def get(db: Session, invoice_id: int) -> EInvoice | None:
    return db.scalar(select(EInvoice).where(EInvoice.invoice_id == invoice_id))


def to_dict(db: Session, e: EInvoice | None, invoice_id: int) -> dict:
    inv_no = db.execute(text("SELECT number FROM sales_invoices WHERE id = :i"), {"i": invoice_id}).scalar()
    if not e:
        return {"invoice_id": invoice_id, "invoice_number": inv_no, "status": "NOT_REPORTED", "mode": (get_settings().einvoice_mode or "off").upper()}
    return {"invoice_id": invoice_id, "invoice_number": inv_no, "status": e.status, "mode": e.mode, "provider": e.provider, "irn": e.irn, "ack_no": e.ack_no,
            "ack_date": e.ack_date.isoformat() if e.ack_date else None, "signed_qr": e.signed_qr, "ewb_no": e.ewb_no, "ewb_valid_until": e.ewb_valid_until.isoformat() if e.ewb_valid_until else None,
            "cancelled_at": e.cancelled_at.isoformat() if e.cancelled_at else None, "cancel_reason": e.cancel_reason,
            "notice": "SANDBOX: not registered with any government portal; has no legal value." if e.mode == "SANDBOX" else None}


def preview(db: Session, invoice_id: int) -> dict:
    payload, errs = build(db, invoice_id)
    return {"mode": (get_settings().einvoice_mode or "off").upper(), "valid": not errs, "errors": errs, "payload": {k: v for k, v in payload.items() if not k.startswith("_")}}


def generate(db: Session, cu: CurrentUser, invoice_id: int) -> dict:
    prov = provider()
    row = db.scalar(select(EInvoice).where(EInvoice.invoice_id == invoice_id).with_for_update())
    if row and row.status == "GENERATED":
        return to_dict(db, row, invoice_id)                                   # idempotent: the same IRN, never a second
    if row and row.status == "CANCELLED":
        raise BusinessConflict("irn_cancelled", "This document number already had its IRN cancelled; the portal does not allow the same number again. Issue a corrected invoice.")
    payload, errs = build(db, invoice_id)
    if errs:
        raise BusinessConflict("einvoice_invalid", errs[0] + (f" (and {len(errs) - 1} more)" if len(errs) > 1 else ""), errors=errs)
    reg = prov.register(payload)
    if db.scalar(select(EInvoice.id).where(EInvoice.irn == reg.irn, EInvoice.invoice_id != invoice_id)):
        raise BusinessConflict("duplicate_irn", "That IRN already exists for another invoice")
    if row is None:
        row = EInvoice(invoice_id=invoice_id, provider=prov.name, mode=prov.mode, payload_hash=_hash(payload), created_by=cu.id)
        db.add(row)
    row.status, row.irn, row.ack_no, row.ack_date, row.signed_qr, row.payload_hash, row.error = "GENERATED", reg.irn, reg.ack_no, reg.ack_date, reg.signed_qr, _hash(payload), None
    db.flush()
    audit.record(db, user=cu, action="einvoice_generate", entity="sales_invoice", entity_id=invoice_id, after={"irn": reg.irn, "mode": prov.mode, "ack_no": reg.ack_no})
    activity.emit(db, "accounts.einvoice.generated", entity="sales_invoice", entity_id=invoice_id, number=payload["DocDtls"]["No"], actor_id=cu.id, mode=prov.mode)
    return to_dict(db, row, invoice_id)


def cancel(db: Session, cu: CurrentUser, invoice_id: int, reason: str) -> dict:
    prov = provider()
    row = db.scalar(select(EInvoice).where(EInvoice.invoice_id == invoice_id).with_for_update())
    if not row or row.status != "GENERATED":
        raise BusinessConflict("not_generated", "There is no active IRN for this invoice")
    if row.ewb_no:
        raise BusinessConflict("ewb_exists", "An e-way bill exists for this invoice; cancel it first")
    if datetime.now(timezone.utc) - row.ack_date > CANCEL_WINDOW:
        raise BusinessConflict("cancel_window_passed", "An IRN can only be cancelled within 24 hours of its acknowledgement; issue a credit note instead")
    prov.cancel(row.irn, reason)
    row.status, row.cancelled_at, row.cancel_reason = "CANCELLED", datetime.now(timezone.utc), reason
    audit.record(db, user=cu, action="einvoice_cancel", entity="sales_invoice", entity_id=invoice_id, after={"irn": row.irn}, reason=reason)
    activity.emit(db, "accounts.einvoice.cancelled", entity="sales_invoice", entity_id=invoice_id, number=to_dict(db, row, invoice_id)["invoice_number"], actor_id=cu.id)
    db.flush()
    return to_dict(db, row, invoice_id)


def eway_bill(db: Session, cu: CurrentUser, invoice_id: int, distance_km: int, vehicle_no: str | None) -> dict:
    prov = provider()
    row = db.scalar(select(EInvoice).where(EInvoice.invoice_id == invoice_id).with_for_update())
    if not row or row.status != "GENERATED":
        raise BusinessConflict("not_generated", "Generate the e-invoice first")
    if row.ewb_no:
        return to_dict(db, row, invoice_id)
    total = db.execute(text("SELECT total FROM sales_invoices WHERE id = :i"), {"i": invoice_id}).scalar_one()
    if total < EWB_THRESHOLD:
        raise BusinessConflict("below_threshold", f"An e-way bill is needed for consignments above Rs {EWB_THRESHOLD:,.0f}; this invoice is Rs {total:,.2f}")
    no, until = prov.eway(row.irn, {"Distance": distance_km, "VehNo": vehicle_no})
    row.ewb_no, row.ewb_valid_until = no, until
    audit.record(db, user=cu, action="eway_bill", entity="sales_invoice", entity_id=invoice_id, after={"ewb_no": no, "mode": row.mode})
    db.flush()
    return to_dict(db, row, invoice_id)


def status(db: Session) -> dict:
    mode = (get_settings().einvoice_mode or "off").lower()
    g = get_setting(db, "company.gstin")
    return {"mode": mode.upper(), "company_gstin_set": bool(g), "company_gstin_valid": valid_gstin(g), "live_available": False,
            "notice": {"off": "E-invoicing is switched off.", "sandbox": "SANDBOX: invoices get a real IRN computation but are NOT registered with any government portal and have no legal value.",
                       "live": "LIVE mode needs a provider adapter that is not installed."}[mode if mode in ("off", "sandbox", "live") else "off"]}


# ------------------------------------------------------------------------------------------------------------------------------- routes
class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=200)


class EwbIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    distance_km: int = Field(100, ge=1, le=4000)
    vehicle_no: str | None = Field(None, max_length=20)


@router.get("/status")
def status_route(db: Session = DB, _: CurrentUser = Depends(require("accounts.einvoice:read"))):
    return status(db)


@router.get("/pending")
def pending_route(limit: int = Query(50, ge=1, le=200), db: Session = DB, _: CurrentUser = Depends(require("accounts.einvoice:read"))):
    rows = db.execute(text("""SELECT i.id, i.number, i.invoice_date, i.total, c.name AS customer, (c.gstin IS NOT NULL AND c.gstin <> '') AS has_gstin
                                FROM sales_invoices i JOIN customers c ON c.id = i.customer_id LEFT JOIN einvoices e ON e.invoice_id = i.id
                               WHERE e.id IS NULL AND c.gstin IS NOT NULL AND c.gstin <> '' ORDER BY i.id DESC LIMIT :n"""), {"n": limit}).mappings().all()
    return {"items": [{**dict(r), "total": str(r["total"])} for r in rows]}


@router.get("/{invoice_id}")
def get_route(invoice_id: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.einvoice:read"))):
    return to_dict(db, get(db, invoice_id), invoice_id)


@router.get("/{invoice_id}/preview")
def preview_route(invoice_id: int, db: Session = DB, _: CurrentUser = Depends(require("accounts.einvoice:read"))):
    return preview(db, invoice_id)


@router.post("/{invoice_id}/generate")
def generate_route(invoice_id: int, db: Session = DB, cu: CurrentUser = Depends(require("accounts.einvoice:submit"))):
    return run_tx(db, lambda d: generate(d, cu, invoice_id), name="accounts.einvoice.generate")


@router.post("/{invoice_id}/cancel")
def cancel_route(invoice_id: int, body: CancelIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.einvoice:cancel"))):
    return run_tx(db, lambda d: cancel(d, cu, invoice_id, body.reason), name="accounts.einvoice.cancel")


@router.post("/{invoice_id}/eway-bill")
def eway_route(invoice_id: int, body: EwbIn, db: Session = DB, cu: CurrentUser = Depends(require("accounts.einvoice:submit"))):
    return run_tx(db, lambda d: eway_bill(d, cu, invoice_id, body.distance_km, body.vehicle_no), name="accounts.einvoice.ewb")
