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


# ── Onboarding wizard (operator surface: register + grant + policies in one shot) ──
def test_oem_catalog_lists_vendors_scopes_and_defaults():
    cat = client.get("/api/dashboard/oem-catalog").json()
    vendors = {v["vendor"] for v in cat["vendors"]}
    assert "Unitree" in vendors and "Boston Dynamics" in vendors
    assert {s["value"] for s in cat["scopes"]} >= {"telemetry.read", "control.velocity"}
    assert "ros2" in cat["transports"]
    assert cat["default_policies"]["max_speed_mps"] > 0


def test_wizard_onboard_grants_scopes_and_sets_policies():
    r = client.post("/api/dashboard/oems", json={
        "company_name": "Wizard Co", "vendor": "Unitree",
        "contact_email": "ops@wizard.example", "transport": "ros2",
        "scopes": ["telemetry.read", "control.velocity", "control.estop"],
        "policies": {"max_speed_mps": 1.2, "drift_halt_threshold_m": 0.4,
                     "auto_estop_on_critical": True, "require_approval_for_teleop": False,
                     "geofence": "zone-a"},
    })
    assert r.status_code == 201
    prof, cred = r.json()["profile"], r.json()["credential"]
    assert cred["api_key"].startswith("orb_")
    assert prof["status"] == "active" and prof["control_ready"] is True
    assert prof["policies"]["max_speed_mps"] == 1.2
    assert prof["policies"]["geofence"] == "zone-a"


def test_operator_can_remove_partner():
    out = client.post("/api/dashboard/oems", json={
        "company_name": "Temp Co", "vendor": "Unitree",
        "contact_email": "t@temp.example", "transport": "ros2",
    }).json()
    oem_id = out["profile"]["oem_id"]
    assert client.get(f"/api/dashboard/oems/{oem_id}").status_code == 200
    assert client.delete(f"/api/dashboard/oems/{oem_id}").status_code == 200
    assert client.get(f"/api/dashboard/oems/{oem_id}").status_code == 404
    assert client.delete(f"/api/dashboard/oems/{oem_id}").status_code == 404


def test_wizard_onboard_drops_scopes_outside_ceiling():
    # Boston Dynamics can't expose control.velocity — onboarding must drop it.
    r = client.post("/api/dashboard/oems", json={
        "company_name": "Spot Co", "vendor": "Boston Dynamics",
        "contact_email": "ops@spot.example", "transport": "grpc",
        "scopes": ["control.velocity", "control.estop"],
    })
    granted = r.json()["profile"]["granted_scopes"]
    assert "control.estop" in granted and "control.velocity" not in granted
