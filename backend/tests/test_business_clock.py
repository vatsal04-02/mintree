"""Business-date rules: one authoritative zone (Asia/Kolkata), Python and SQL agree, no banned date sources."""
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

from app.accounts import reports
from app.core import clock
from tests.test_accounts import fin  # noqa: F401
from tests.test_dispatch import make_customer  # noqa: F401
from tests.test_production import k  # noqa: F401

IST = timezone(timedelta(hours=5, minutes=30))
APP = Path(__file__).resolve().parent.parent / "app"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# (UTC instant, expected Indian business date)
BOUNDARIES = [
    (utc(2026, 11, 6, 18, 29, 59), date(2026, 11, 6)),   # 23:59:59 IST -- still the 6th
    (utc(2026, 11, 6, 18, 30, 0), date(2026, 11, 7)),    # exactly 00:00:00 IST -- the 7th
    (utc(2026, 11, 6, 18, 30, 1), date(2026, 11, 7)),    # just after midnight IST
    (utc(2026, 11, 6, 20, 0, 0), date(2026, 11, 7)),     # UTC date (6th) differs from Indian date (7th)
    (utc(2026, 11, 6, 0, 0, 0), date(2026, 11, 6)),      # 05:30 IST: same date both sides
    (utc(2026, 1, 31, 19, 0, 0), date(2026, 2, 1)),      # month-end rollover in IST
    (utc(2026, 3, 31, 18, 30, 0), date(2026, 4, 1)),     # financial-year boundary (FY starts 1 April)
    (utc(2026, 12, 31, 18, 30, 0), date(2027, 1, 1)),    # year-end rollover in IST
    (utc(2028, 2, 28, 18, 30, 0), date(2028, 2, 29)),    # leap day
]


@pytest.mark.parametrize("ts,expected", BOUNDARIES)
def test_python_business_date(ts, expected):
    assert clock.business_date(ts) == expected
    assert clock.business_date(ts.astimezone(IST)) == expected      # same instant, other offset
    assert clock.business_date(ts.astimezone(timezone(timedelta(hours=-8)))) == expected


@pytest.mark.parametrize("ts,expected", BOUNDARIES)
@pytest.mark.parametrize("session_tz", ["UTC", "Asia/Kolkata", "Pacific/Kiritimati", "America/Los_Angeles"])
def test_sql_business_date_matches_python_and_ignores_session_timezone(db, ts, expected, session_tz):
    db.execute(text(f"SET LOCAL timezone = '{session_tz}'"))
    assert db.scalar(text("SELECT business_date(:t)"), {"t": ts}) == expected


def test_sql_zone_equals_python_zone(db):
    src = (Path(__file__).resolve().parent.parent / "migrations/versions/20261106_0015_business_timezone.py").read_text()
    assert f"'{clock.BUSINESS_TZ_NAME}'" in src
    # business_today() is business_date(now()) and agrees with Python at the same moment (retry once across a midnight edge)
    for _ in range(2):
        py, sql = clock.business_today(), db.scalar(text("SELECT business_today()"))
        if py == sql:
            break
    assert py == sql


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        clock.business_date(datetime(2026, 11, 6, 12, 0))


def test_connections_pin_utc(db):
    assert db.scalar(text("SHOW timezone")) == "UTC"


def test_ageing_is_relative_to_the_supplied_business_date(fin, db):
    """Payables ageing: invoice dated D, 30-day terms. days_overdue = as_of - (D + 30); no hidden clock inside the report.
    Sweeping as_of across a day, month and year boundary moves the answer by exactly one day per day."""
    from tests.test_accounts_gaps import supplier_invoice
    m, inv = supplier_invoice(fin)
    db.execute(text("UPDATE suppliers SET payment_terms_days = 30 WHERE id = :i"), {"i": m["supplier_id"]})
    db.execute(text("UPDATE supplier_invoices SET invoice_date = :d WHERE id = :i"), {"d": date(2026, 9, 16), "i": inv["id"]})   # due 2026-10-16
    cases = [(date(2026, 11, 5), 20), (date(2026, 11, 6), 21), (date(2026, 11, 7), 22),     # consecutive business days
             (date(2026, 10, 31), 15), (date(2026, 11, 1), 16),                              # month-end -> month-start
             (date(2026, 12, 31), 76), (date(2027, 1, 1), 77), (date(2026, 10, 16), 0), (date(2026, 10, 15), 0)]
    for as_of, expected in cases:
        ag = fin["acct"].get("/api/accounts/reports/ageing/payables", params={"as_of": str(as_of)}).json()
        row = next(p for p in ag["parties"] if p["party_id"] == m["supplier_id"])
        assert row["invoices"][0]["days_overdue"] == expected, (as_of, row)


def test_default_as_of_is_the_business_date_not_the_server_date(fin, db, monkeypatch):
    """With the process clock mocked to 20:00 UTC on the 6th (01:30 IST on the 7th) the default 'today' is the 7th."""
    from tests.test_accounts_gaps import supplier_invoice
    m, inv = supplier_invoice(fin)
    db.execute(text("UPDATE suppliers SET payment_terms_days = 0 WHERE id = :i"), {"i": m["supplier_id"]})
    db.execute(text("UPDATE supplier_invoices SET invoice_date = :d WHERE id = :i"), {"d": date(2026, 11, 1), "i": inv["id"]})
    monkeypatch.setattr(clock, "utc_now", lambda: utc(2026, 11, 6, 20, 0, 0))
    ag = fin["acct"].get("/api/accounts/reports/ageing/payables").json()
    assert ag["as_of"] == "2026-11-07"
    row = next(p for p in ag["parties"] if p["party_id"] == m["supplier_id"])
    assert row["invoices"][0]["days_overdue"] == 6


def test_no_banned_date_sources_in_application_code():
    """date.today(), current_date and CURRENT_DATE depend on the OS / DB session clock; use app.core.clock instead."""
    # also: casting a timestamptz column to date uses the DB session zone, not the business zone (use business_date(col))
    bad = re.compile(r"\bdate\.today\(\)|\bcurrent_date\b|\bCURRENT_DATE\b|\bdatetime\.now\(\)|\bdatetime\.utcnow\(\)|_at\)?::date", re.I)
    offenders = []
    for p in APP.rglob("*.py"):
        if p.name == "clock.py":
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if bad.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{p.relative_to(APP.parent)}:{n}: {line.strip()[:90]}")
    assert not offenders, "\n".join(offenders)
