"""Console sign-in: an operator key becomes a session cookie that unlocks control."""
import pytest
from fastapi.testclient import TestClient

from orbital_cloud import sessions
from orbital_cloud.main import app
from orbital_cloud.routers import auth


@pytest.fixture
def rbac(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "admin:adm-key-123,operator:ops-key-456,agent:bot-key")
    monkeypatch.delenv("ORBITAL_SESSION_SECRET", raising=False)
    auth._login_limiter.reset()
    return TestClient(app)


def _estop(client):
    return client.post("/api/dashboard/robot/rbt-01/estop?dry_run=true")


def test_anonymous_is_viewer_until_signed_in(rbac):
    assert _estop(rbac).status_code == 403
    assert rbac.get("/api/auth/me").json() == {
        "sign_in_available": True, "signed_in": False, "role": None, "actor": None, "expires_at": None}

    r = rbac.post("/api/auth/login", json={"key": "ops-key-456", "name": "Ugo"})
    assert r.status_code == 200 and r.json()["role"] == "operator"
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "SameSite=strict" in set_cookie
    assert "ops-key-456" not in set_cookie

    me = rbac.get("/api/auth/me").json()
    assert me["signed_in"] and me["actor"] == "console:Ugo"
    assert _estop(rbac).status_code == 200
    assert rbac.get("/api/dashboard/whoami").json()["role"] == "operator"

    rbac.post("/api/auth/logout")
    assert _estop(rbac).status_code == 403


def test_control_audit_names_the_signed_in_operator(rbac):
    rbac.post("/api/auth/login", json={"key": "adm-key-123", "name": "Ugo"})
    _estop(rbac)
    events = rbac.get("/api/dashboard/control-audit?limit=5").json()["events"]
    assert events[0]["actor"] == "console:Ugo" and events[0]["action"] == "estop"


@pytest.mark.parametrize("key", ["wrong", "bot-key"])
def test_wrong_or_agent_key_cannot_sign_in(rbac, key):
    r = rbac.post("/api/auth/login", json={"key": key})
    assert r.status_code == 401
    assert "set-cookie" not in r.headers


def test_forged_or_tampered_cookie_is_ignored(rbac):
    rbac.post("/api/auth/login", json={"key": "ops-key-456"})
    good = rbac.cookies.get(sessions.COOKIE_NAME)
    payload, sig = good.rsplit(".", 1)
    forged = sessions._b64(b'{"r":"admin","a":"x","e":9999999999}') + "." + sig
    for bad in (forged, payload + ".AAAA", "garbage"):
        rbac.cookies.set(sessions.COOKIE_NAME, bad)
        assert _estop(rbac).status_code == 403


def test_rotating_keys_signs_everyone_out(rbac, monkeypatch):
    rbac.post("/api/auth/login", json={"key": "ops-key-456"})
    assert _estop(rbac).status_code == 200
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "operator:new-key-789")
    assert _estop(rbac).status_code == 403


def test_expired_session_is_ignored(rbac):
    rbac.cookies.set(sessions.COOKIE_NAME, sessions.issue("operator", "console:x", ttl_s=-1))
    assert _estop(rbac).status_code == 403


def test_login_is_rate_limited(rbac):
    for _ in range(auth._LOGIN_ATTEMPTS_PER_MIN):
        rbac.post("/api/auth/login", json={"key": "wrong"})
    assert rbac.post("/api/auth/login", json={"key": "ops-key-456"}).status_code == 429


def test_sign_in_unconfigured_explains_itself(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.delenv("ORBITAL_RBAC_TOKENS", raising=False)
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"key": "anything"})
    assert r.status_code == 503 and "ORBITAL_RBAC_TOKENS" in r.json()["detail"]
    assert client.get("/api/auth/me").json()["sign_in_available"] is False
