import httpx
import pytest

from app.core import resilience as r


def resp(code):
    return httpx.Response(code)


def test_transient_failures_are_retried_with_backoff_then_succeed():
    b, naps, seq = r.Breaker("t"), [], iter([503, 502, 200])
    out = r.call(b, lambda: resp(next(seq)), sleep=naps.append)
    assert out.status_code == 200 and len(naps) == 2 and naps[1] > naps[0] and b.state == "closed"


def test_client_errors_are_not_retried_and_do_not_trip_the_breaker():
    b, calls = r.Breaker("t"), []
    out = r.call(b, lambda: (calls.append(1), resp(401))[1], sleep=lambda s: None)
    assert out.status_code == 401 and len(calls) == 1 and b.state == "closed"


def test_breaker_opens_after_repeated_failures_then_fails_fast_then_recovers():
    b = r.Breaker("t", threshold=2, reset_s=0.05)

    def boom():
        raise httpx.ConnectError("down")
    for _ in range(2):
        with pytest.raises(httpx.ConnectError):
            r.call(b, boom, retries=1, sleep=lambda s: None)
    assert b.state == "open"
    calls = []
    with pytest.raises(r.CircuitOpen):
        r.call(b, lambda: calls.append(1) or resp(200))
    assert not calls, "an open breaker must not touch the dependency"
    import time
    time.sleep(0.25)
    assert b.state == "half-open"
    assert r.call(b, lambda: resp(200)).status_code == 200 and b.state == "closed"


def test_only_one_probe_is_allowed_while_half_open():
    b = r.Breaker("t", threshold=1, reset_s=0.01)
    b.failure()
    import time
    time.sleep(0.25)
    b.before()                                 # first caller becomes the probe
    with pytest.raises(r.CircuitOpen):
        b.before()                             # everyone else keeps failing fast


def test_final_5xx_is_returned_to_the_caller_for_its_own_message():
    b = r.Breaker("t")
    assert r.call(b, lambda: resp(503), retries=1, sleep=lambda s: None).status_code == 503
