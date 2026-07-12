"""OEM onboarding — register, unlock scopes (bounded by ceiling), auth, profile, revoke."""
from fastapi.testclient import TestClient

from orbital_cloud.main import app

client = TestClient(app)


def _register(vendor: str = "Unitree") -> dict:
    r = client.post("/api/oem/register", json={
        "company_name": f"{vendor} Co", "vendor": vendor,
        "contact_email": "ops@example.com", "transport": "ros2",
    })
    assert r.status_code == 201
    return r.json()


def test_register_issues_key_and_ceiling():
    out = _register("Unitree")
    assert out["partner"]["status"] == "pending"
    assert out["credential"]["api_key"].startswith("orb_")
    assert "control.velocity" in out["partner"]["ceiling_scopes"]


def test_unlock_requires_matching_api_key():
    out = _register("Unitree")
    oem_id = out["partner"]["id"]
    # No key → 401
    assert client.post(f"/api/oem/{oem_id}/scopes", json={"scopes": ["telemetry.read"]}).status_code == 401
    # Wrong key → 401
    bad = client.post(f"/api/oem/{oem_id}/scopes", json={"scopes": ["telemetry.read"]},
                      headers={"authorization": "Bearer orb_deadbeef_nope"})
    assert bad.status_code == 401


def test_unlock_grants_and_activates():
    out = _register("Unitree")
    oem_id, key = out["partner"]["id"], out["credential"]["api_key"]
    r = client.post(f"/api/oem/{oem_id}/scopes",
                    json={"scopes": ["telemetry.read", "control.velocity", "control.estop"]},
                    headers={"authorization": f"Bearer {key}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert set(body["granted_scopes"]) >= {"telemetry.read", "control.velocity", "control.estop"}


def test_ceiling_blocks_ungrantable_scope():
    # Boston Dynamics can't expose control.velocity — the grant must drop it.
    out = _register("Boston Dynamics")
    oem_id, key = out["partner"]["id"], out["credential"]["api_key"]
    r = client.post(f"/api/oem/{oem_id}/scopes",
                    json={"scopes": ["control.velocity", "control.estop"]},
                    headers={"authorization": f"Bearer {key}"})
    granted = r.json()["granted_scopes"]
    assert "control.estop" in granted and "control.velocity" not in granted


def test_profile_reports_readiness():
    out = _register("Unitree")
    oem_id, key = out["partner"]["id"], out["credential"]["api_key"]
    client.post(f"/api/oem/{oem_id}/scopes",
                json={"scopes": ["telemetry.read", "control.velocity", "control.estop"]},
                headers={"authorization": f"Bearer {key}"})
    profile = client.get(f"/api/oem/{oem_id}/profile").json()
    assert profile["monitor_ready"] is True
    assert profile["control_ready"] is True


def test_revoke_downgrades_status():
    out = _register("Unitree")
    oem_id, key = out["partner"]["id"], out["credential"]["api_key"]
    hdr = {"authorization": f"Bearer {key}"}
    client.post(f"/api/oem/{oem_id}/scopes", json={"scopes": ["telemetry.read"]}, headers=hdr)
    r = client.request("DELETE", f"/api/oem/{oem_id}/scopes",
                       json={"scopes": ["telemetry.read"]}, headers=hdr)
    body = r.json()
    assert body["granted_scopes"] == [] and body["status"] == "pending"
