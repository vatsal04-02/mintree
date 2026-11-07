"""GET /analytics/overview-period: period revenue (with the previous period), the weekly production series (no-data weeks are null, not zero) and the recent-documents list."""
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.clock import business_today
from tests.test_dispatch import advance, make_customer, make_fg, new_so, shop  # noqa: F401
from tests.test_production import k, plant  # noqa: F401


def get(c, start, end):
    return c.get("/api/analytics/overview-period", params={"start": str(start), "end": str(end)})


def test_validation_and_permissions(login, anon):
    t = business_today()
    m = login("management")
    assert get(m, t, t).status_code == 200
    assert get(m, t, t - timedelta(days=1)).status_code == 422
    assert get(m, t - timedelta(days=500), t).status_code == 422
    assert get(login("production_operator"), t, t).status_code == 403
    assert get(anon, t, t).status_code == 401


def test_revenue_series_and_recent_reconcile_with_the_tables(shop, db):
    f, cust = make_fg(shop, 100, 10), make_customer()
    o = new_so(shop, f, cust, qty="30", price="25")
    d = shop["disp"].post(f"/api/dispatch/orders/{o['id']}/dispatch", headers=k(), json={"expected_version": advance(shop, o)["version"]}).json()
    t = business_today()
    assert get(shop["disp"], t - timedelta(days=30), t).status_code == 403          # a role without analytics.dashboard:read sees nothing
    body = get(shop["admin"], t - timedelta(days=30), t).json()
    sub = db.scalar(text("SELECT COALESCE(sum(subtotal),0) FROM sales_invoices WHERE invoice_date BETWEEN :a AND :b"), {"a": t - timedelta(days=30), "b": t})
    cn = db.scalar(text("SELECT COALESCE(sum(subtotal),0) FROM credit_notes WHERE business_date(created_at) BETWEEN :a AND :b"), {"a": t - timedelta(days=30), "b": t})
    assert Decimal(str(body["revenue"]["net_ex_tax"])) == sub - cn and body["revenue"]["invoices"] >= 1
    assert body["previous"]["end"] == str(t - timedelta(days=31))
    # production: the order the fixture completed today is in this week's bucket with its real quantities
    wk = t - timedelta(days=t.weekday())
    b = next(x for x in body["production"]["buckets"] if x["week_start"] == str(wk))
    assert b["orders"] >= 1 and Decimal(b["actual"]) > 0
    empty = [x for x in body["production"]["buckets"] if x["orders"] == 0]
    assert all(x["planned"] is None and x["actual"] is None for x in empty)       # no data is null, never a fake zero
    assert any(x["reference"] == d["invoice"]["number"] and x["kind"] == "sales_invoice" for x in body["recent"])
    assert len(body["recent"]) <= 8 and [x["created_at"] for x in body["recent"]] == sorted((x["created_at"] for x in body["recent"]), reverse=True)
