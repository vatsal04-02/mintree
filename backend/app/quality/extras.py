"""Quality extras: Certificate of Analysis (PDF) for a released inspection, and batch disposition history."""
import io
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.approvals.service import get_setting
from app.core.errors import BusinessConflict, NotFound
from app.quality import service as q
from app.quality.models import Inspection, QualitySpec


def coa_pdf(db: Session, inspection_id: int) -> tuple[str, bytes]:
    """Only a RELEASED inspection yields a COA (a COA is a statement of conformity). Uses the latest result per test; earlier retests remain in the database."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    i = db.get(Inspection, inspection_id)
    if not i:
        raise NotFound("Inspection")
    if i.decision != "RELEASE":
        raise BusinessConflict("not_released", "A certificate of analysis is issued only for a released inspection")
    spec = db.get(QualitySpec, i.spec_id)
    b = db.execute(text("""SELECT ib.batch_no, ib.mfg_date, ib.expiry_date, it.item_code, it.name FROM inventory_batches ib JOIN items it ON it.id = ib.item_id WHERE ib.id = :b"""), {"b": i.batch_id}).mappings().one()
    who = db.execute(text("SELECT full_name FROM users WHERE id = :u"), {"u": i.decided_by}).scalar()
    res = q.latest_results(db, i.id)
    st = getSampleStyleSheet()
    small = st["BodyText"].clone("small", fontSize=8, leading=10)
    rows = [["Test", "Method", "Specification", "Result", "Unit", "Status"]]
    for t in spec.tests:
        r = res.get(t.test_name)
        if t.is_pass_fail:
            lim = "Complies"
        elif t.min_value is not None and t.max_value is not None:
            lim = f"{t.min_value:f} – {t.max_value:f}"
        elif t.min_value is not None:
            lim = f"≥ {t.min_value:f}"
        else:
            lim = f"≤ {t.max_value:f}"
        rows.append([t.test_name, t.method or "-", lim, "-" if r is None else ("Complies" if t.is_pass_fail and r.passed else (f"{r.value:f}" if r.value is not None else ("Fails" if not r.passed else "-"))), t.unit or "", "-" if r is None else ("PASS" if r.passed else "FAIL")])
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=f"COA {i.number}")
    t = Table(rows, repeatRows=1, colWidths=[40 * mm, 36 * mm, 34 * mm, 28 * mm, 18 * mm, 18 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#126B61")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E4E8E4"))]))
    doc.build([Paragraph(f"<b>{get_setting(db, 'company.name') or 'Mintree'}</b> — Certificate of Analysis", st["Title"]),
               Paragraph(f"Certificate: <b>{i.number}</b> &nbsp; Stage: {i.stage}<br/>Product: <b>{b['item_code']} {b['name']}</b><br/>Batch: <b>{b['batch_no']}</b> &nbsp; Mfg: {b['mfg_date'] or '-'} &nbsp; Expiry: {b['expiry_date'] or '-'}", small),
               Spacer(1, 8), t, Spacer(1, 10),
               Paragraph(f"Disposition: <b>RELEASED</b> by {who or '-'} on {i.decided_at:%Y-%m-%d %H:%M} UTC. Specification version {spec.version_no}.", small)])
    return i.number, buf.getvalue()


def dispositions(db: Session, batch_id: int) -> dict:
    """Everything that decided this batch's fate, in order: inspections with decisions and reasons, plus the batch's current QC status."""
    b = db.execute(text("SELECT id, batch_no, qc_status, qc_decided_at FROM inventory_batches WHERE id = :b"), {"b": batch_id}).mappings().first()
    if not b:
        raise NotFound("Batch")
    insp = db.execute(text("""SELECT n.number, n.stage, n.status, n.decision, n.decision_reason, n.decided_at, u.full_name AS decided_by FROM inspections n LEFT JOIN users u ON u.id = n.decided_by
                               WHERE n.batch_id = :b ORDER BY n.id"""), {"b": batch_id}).mappings().all()
    devs = db.execute(text("SELECT number, title, classification, status FROM deviations WHERE batch_id = :b ORDER BY id"), {"b": batch_id}).mappings().all()
    return {"batch": dict(b), "inspections": [dict(x) for x in insp], "deviations": [dict(x) for x in devs]}
