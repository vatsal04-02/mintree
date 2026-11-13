"""Demo Live Mode safety, readiness, and the event stream's access rules. The engine itself is exercised against a real demo database by `make demo-check`."""
import pytest

from app.core import readiness
from app.core.config import get_settings
from app.demo_live import guard
from app.demo_live.router import telemetry


def _settings(monkeypatch, **kw):
    s = get_settings()
    for k, v in kw.items():
        monkeypatch.setattr(s, k, v)


def test_live_mode_is_off_by_default_and_refuses_outside_a_demo_database(monkeypatch):
    assert guard.refusal() is not None                                           # flag off
    _settings(monkeypatch, demo_live=True)
    assert "must be exactly 'demo'" in guard.refusal()                           # test environment is not demo
    _settings(monkeypatch, environment="production")
    assert "production" in guard.refusal()
    _settings(monkeypatch, environment="demo", database_url="postgresql+psycopg://u:p@localhost/mintree")
    assert "not recognisably a demo database" in guard.refusal()
    _settings(monkeypatch, database_url="postgresql+psycopg://u:p@localhost/mintree_demo")
    assert guard.refusal() is None                                               # all three conditions hold
    with pytest.raises(SystemExit):
        _settings(monkeypatch, environment="production")
        guard.assert_safe()


def test_engine_refuses_to_start_against_a_non_demo_database():
    from app.demo_live.engine import main
    with pytest.raises(SystemExit):
        main()                                                                   # DEMO_LIVE is false in the test environment


def test_status_says_unavailable_and_controls_are_forbidden_outside_demo(login):
    c = login("system_admin")
    st = c.get("/api/demo/status").json()
    assert st == {"available": False}                                              # and no configuration detail leaks to ordinary users
    assert c.post("/api/demo/controls", json={"action": "pause"}, headers={"X-CSRF-Token": c.headers["X-CSRF-Token"]}).status_code == 403
    assert c.post("/api/demo/trigger", json={"kind": "order"}, headers={"X-CSRF-Token": c.headers["X-CSRF-Token"]}).status_code == 403
    assert c.post("/api/demo/reset", headers={"X-CSRF-Token": c.headers["X-CSRF-Token"]}).status_code == 403
    assert c.get("/api/demo/metrics").status_code == 403
    assert c.get("/api/demo/scenario").status_code == 403
    assert c.post("/api/demo/scenario", json={"action": "start"}, headers={"X-CSRF-Token": c.headers["X-CSRF-Token"]}).status_code == 403
    assert c.get("/api/demo/triggers").status_code == 403


def test_event_stream_and_demo_endpoints_need_a_session(anon):
    assert anon.get("/api/events").status_code == 401
    assert anon.get("/api/demo/status").status_code == 401


def test_ready_reports_required_checks_and_degrades_instead_of_failing_for_optional_ones():
    ok, body = readiness.run_checks()
    assert ok is True
    for name in ("database", "migrations", "disk"):
        assert body["checks"][name]["ok"] is True, body
    assert body["status"] in ("ready", "degraded")
    assert body["checks"]["scheduler"]["detail"] == "off"                         # flag off is healthy


def test_ready_endpoint_is_200_when_required_checks_pass(anon):
    r = anon.get("/api/ready")
    assert r.status_code == 200 and r.json()["checks"]["database"]["ok"] is True


def test_a_failing_required_check_makes_ready_503(anon, monkeypatch):
    def boom():
        raise RuntimeError("database is down")
    monkeypatch.setattr(readiness, "CHECKS", [("database", boom, True), ("cache", lambda: "ok", False)])
    r = anon.get("/api/ready")
    assert r.status_code == 503 and r.json()["status"] == "not_ready"


def test_telemetry_is_labelled_simulated_deterministic_and_plausible():
    a = telemetry([{"id": 7, "number": "PRD/1"}], 1000.0)
    assert a == telemetry([{"id": 7, "number": "PRD/1"}], 1000.0)
    assert a[0]["simulated"] is True
    r = a[0]["readings"]
    assert 60 < r["temperature_c"] < 90 and 40 < r["weigh_scale_kg"] < 60 and 30 < r["line_speed_ppm"] < 60


def test_production_refuses_development_defaults():
    from app.core.config import Settings, production_problems
    bad = production_problems(Settings(environment="production", _env_file=None))
    assert any("webhook secrets" in b for b in bad) and any("COOKIE_SECURE" in b for b in bad) and any("RATE_LIMIT" in b for b in bad) and any("default mintree/mintree" in b for b in bad)
    ok = Settings(environment="production", _env_file=None, courier_webhook_secret="x" * 32, marketplace_webhook_secret="y" * 32, cookie_secure=True, rate_limit_enabled=True,
                  database_url="postgresql+psycopg://svc:longsecret@db/mintree")
    assert production_problems(ok) == []
    assert any("DEMO_LIVE" in b for b in production_problems(ok.model_copy(update={"demo_live": True})))


def test_warm_up_primes_the_pool_and_never_raises():
    from app.main import warm_up
    out = warm_up()
    assert out["pool"] >= 1 and out["ms"] >= 0


def test_module_dashboard_cache_serves_within_the_floor_and_rebuilds_after_a_write(monkeypatch, login):
    """Cached module dashboards lag a business write by at most rcache.DASH_FLOOR_S, then rebuild; the test environment bypasses the cache otherwise."""
    from app.core import rcache
    c = login("system_admin")
    a = c.get("/api/dispatch/dashboard").json()
    monkeypatch.setattr(rcache, "TEST_CACHE_ON", True)
    first = c.get("/api/crm/dashboard")
    assert first.headers.get("x-cache") is None and first.status_code == 200
    calls = []
    real = rcache.dash_lookup
    monkeypatch.setattr(rcache, "dash_lookup", lambda *x, **k: (calls.append(1), real(*x, **k))[1])
    c.get("/api/crm/dashboard")
    c.get("/api/crm/dashboard")
    assert len(calls) >= 2 and a is not None
    # a write bumps the version; inside the floor the entry is still served, past the floor it is rebuilt
    ns, key = "mod:crm:dashboard", "{}"
    body, ver = real(ns, key, 15.0)
    assert body is not None
    monkeypatch.setattr(rcache, "_version", lambda name: "v-after-write")
    assert real(ns, key, 15.0)[0] is not None                                   # younger than the floor: still served
    monkeypatch.setattr(rcache, "DASH_FLOOR_S", 0.0)
    assert real(ns, key, 15.0, floor_s=0.0)[0] is None                           # a write happened and the entry is past the floor: rebuilt


def test_every_control_centre_action_is_implemented_and_unknown_ones_are_refused(admin):
    from app.demo_live.actions import Actions
    from app.demo_live.engine import Live
    from app.demo_live.scenario import STEPS
    for kind, target in Actions.KINDS.items():
        assert len(kind) <= 30, "demo_live_triggers.kind is varchar(30)"
        assert kind.startswith("market_") and kind != "market_sync" or hasattr(Live, target), (kind, target)
    for _, fn in STEPS:
        assert hasattr(Live, fn), fn
    r = admin.post("/api/demo/trigger", json={"kind": "drop_all_tables"}, headers={"X-CSRF-Token": admin.headers["X-CSRF-Token"]})
    assert r.status_code == 422, "an unknown action never reaches the queue"


def test_scenario_state_machine_rules():
    """The pure state rules: what start / pause / resume / retry / step / reset may do (no business operations involved)."""
    import pytest
    from app.core.db import SessionLocal
    from app.demo_live import scenario
    with SessionLocal() as db:
        try:
            assert scenario.control(db, "reset")["status"] == "idle"
            with pytest.raises(ValueError):
                scenario.control(db, "pause")                                  # nothing to pause
            assert scenario.control(db, "start")["status"] == "running"
            with pytest.raises(ValueError):
                scenario.control(db, "start")                                  # already running
            assert scenario.control(db, "pause")["status"] == "paused"
            assert scenario.control(db, "resume")["status"] == "running"
            assert scenario.control(db, "speed", 3)["speed"] == 3
            with pytest.raises(ValueError):
                scenario.control(db, "retry")                                  # only a failed scenario can be retried
        finally:
            scenario.control(db, "reset")
            scenario.control(db, "speed", 1)
            db.commit()
