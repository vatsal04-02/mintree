"""The single authority for "what day is it" in Mintree.

Rules (see docs/DATABASE_ARCHITECTURE.md, "Time"):
  * Instants (created_at, posted_at, ...) are TIMESTAMPTZ, always handled as timezone-aware UTC.
  * Calendar dates (invoice_date, due_date, planned_date, expiry_date, ...) are plain DATE values in the
    *business* timezone (Asia/Kolkata). They are never derived from the server's local clock or the
    database session's TimeZone setting.
  * Python code must call `business_today()` / `business_date(ts)`; SQL must call `business_today()` /
    `business_date(ts)` (functions created by migration 0015 with the same zone). `date.today()` and
    `current_date` are banned and enforced by tests/test_business_clock.py.
"""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

BUSINESS_TZ_NAME = "Asia/Kolkata"
BUSINESS_TZ = ZoneInfo(BUSINESS_TZ_NAME)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def business_date(ts: datetime) -> date:
    """The Indian calendar date on which the instant `ts` falls. Naive datetimes are rejected (ambiguous)."""
    if ts.tzinfo is None:
        raise ValueError("business_date() needs a timezone-aware datetime; naive values are ambiguous")
    return ts.astimezone(BUSINESS_TZ).date()


def business_today() -> date:
    return business_date(utc_now())
