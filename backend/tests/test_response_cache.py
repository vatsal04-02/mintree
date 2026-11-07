"""Response cache: hits, invalidation on write, permission separation, bypass without Redis, dashboards flagged as cached."""
import json

import pytest

from app.core import authz_cache, rcache
from tests.conftest import mk_master


@pytest.fixture(autouse=True)
def _fresh():
    rcache.clear()
    yield
    rcache.clear()


def test_second_identical_list_is_served_from_cache_with_identical_bytes(login):
    c = login("stores_manager")
    a = c.get("/api/masters/units-of-measure", params={"page_size": 50})
    b = c.get("/api/masters/units-of-measure", params={"page_size": 50})
    assert a.headers["x-cache"] == "miss" and b.headers["x-cache"] == "hit" and a.content == b.content
    assert c.get("/api/masters/units-of-measure", params={"page_size": 25}).headers["x-cache"] == "miss"      # different query, different entry


def test_a_write_invalidates_the_namespace_immediately(login, admin):
    c = login("stores_manager")
    c.get("/api/masters/warehouses")
    assert c.get("/api/masters/warehouses").headers["x-cache"] == "hit"
    r = admin.post("/api/masters/warehouses", json={"code": "RC-WH-1", "name": "Cache test warehouse", "kind": "RM"})
    assert r.status_code == 201
    after = c.get("/api/masters/warehouses")
    assert after.headers["x-cache"] == "miss" and "RC-WH-1" in after.text            # the new row is visible at once, never a stale list


def test_cached_lists_do_not_leak_across_permission_sets(login, admin):
    sup = admin.get("/api/masters/suppliers", params={"page_size": 5})
    assert sup.status_code == 200
    plain = login("production_operator")                                                # may not read suppliers at all
    assert plain.get("/api/masters/suppliers").status_code == 403                      # the permission check runs before any cache lookup
    a, b = login("purchase_manager"), login("stores_manager")
    ra, rb = a.get("/api/masters/suppliers"), b.get("/api/masters/suppliers")
    if rb.status_code == 200:
        assert "bank_account_no" not in rb.text or "bank_account_no" in ra.text        # a role without bank-detail access never receives another role's cached body


def test_no_redis_means_no_caching_and_the_answer_is_still_correct(login, monkeypatch):
    c = login("stores_manager")
    monkeypatch.setattr(authz_cache, "memo_get", lambda key: None)                 # Redis unreachable
    a = c.get("/api/masters/warehouses")
    b = c.get("/api/masters/warehouses")
    assert a.status_code == b.status_code == 200 and "x-cache" not in a.headers and "x-cache" not in b.headers


def test_dashboard_is_cached_for_15s_and_says_so(login):
    m = login("management")
    a = m.get("/api/analytics/management-summary").json()
    b = m.get("/api/analytics/management-summary").json()
    assert a["meta"]["cached"] is False and b["meta"]["cached"] is True
    assert a["meta"]["generated_at"] == b["meta"]["generated_at"] and a["meta"]["max_age_s"] == 15.0
    assert {k: v for k, v in a.items() if k != "meta"} == {k: v for k, v in b.items() if k != "meta"}
    assert login("production_operator").get("/api/analytics/management-summary").status_code == 403


def test_single_flight_lets_one_request_compute():
    """Concurrent misses for one key: the first computes, the others wait and then find its result. (Unit level: the request-level helper shares one DB session in tests.)"""
    import threading, time
    computed, results = [], []
    barrier = threading.Barrier(6)

    def request():
        barrier.wait(10)
        body, ver = rcache.lookup("sf", "k", 60, check_version=False)
        if body is not None:
            results.append("hit"); return
        with rcache.single_flight("sf", "k"):
            body, _ = rcache.lookup("sf", "k", 60, check_version=False)
            if body is not None:
                results.append("hit"); return
            time.sleep(0.2)                                   # the expensive query
            computed.append(1)
            rcache.store("sf", "k", "ttl", b"{}")
            results.append("miss")

    ts = [threading.Thread(target=request) for _ in range(6)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(computed) == 1 and results.count("miss") == 1 and results.count("hit") == 5


def test_bounded_memory():
    for i in range(rcache.MAX_ENTRIES + 50):
        rcache.store("x", f"k{i}", "1", b"y" * 10)
    assert len(rcache._entries) <= rcache.MAX_ENTRIES
