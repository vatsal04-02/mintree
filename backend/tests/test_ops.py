"""Operational safeguards: rate limiting."""
import pytest

from app.core import middleware
from app.core.config import get_settings


@pytest.fixture
def limits():
    s = get_settings()
    old = (s.rate_limit_enabled, s.rate_limit_login_per_min, s.rate_limit_webhook_per_min, s.trust_forwarded_for)
    middleware._local.clear()
    try:
        r = middleware._rl_client()
        for k in r.scan_iter("mintree:rl:*"):
            r.delete(k)
    except Exception:
        pass
    yield s
    s.rate_limit_enabled, s.rate_limit_login_per_min, s.rate_limit_webhook_per_min, s.trust_forwarded_for = old
    middleware._local.clear()
    try:
        r = middleware._rl_client()
        for k in r.scan_iter("mintree:rl:*"):
            r.delete(k)
    except Exception:
        pass


def test_login_is_rate_limited_per_ip_and_recovers_next_window(anon, limits, monkeypatch):
    limits.rate_limit_enabled, limits.rate_limit_login_per_min = True, 3
    bad = lambda: anon.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert [bad().status_code for _ in range(3)] == [401, 401, 401]
    r = bad()
    assert r.status_code == 429 and int(r.headers["retry-after"]) >= 1 and "Too many requests" in r.json()["detail"]
    other = anon.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"}, headers={"X-Forwarded-For": "203.0.113.9"})
    assert other.status_code == 429, "X-Forwarded-For is ignored unless a trusted proxy is configured (it could be spoofed)"
    limits.trust_forwarded_for = True
    assert anon.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"}, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 401, "a different client IP has its own budget"
    # the next minute starts a new window
    real = middleware.time.time
    monkeypatch.setattr(middleware.time, "time", lambda: real() + 61)
    limits.trust_forwarded_for = False
    assert bad().status_code == 401


def test_other_endpoints_and_disabled_mode_are_not_limited(anon, limits):
    limits.rate_limit_login_per_min = 1
    limits.rate_limit_enabled = False
    assert all(anon.post("/api/auth/login", json={"email": "x@example.com", "password": "wrong-password-1"}).status_code == 401 for _ in range(5))
    limits.rate_limit_enabled = True
    assert all(anon.get("/api/health").status_code == 200 for _ in range(5)), "only login and webhooks are limited"
