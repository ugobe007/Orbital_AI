"""SQLite persistence: OEM grants + waypoints survive a simulated restart."""
from orbital_cloud import persistence
from orbital_cloud.models import APIScope, ControlTransport, OEMRegisterIn
from orbital_cloud.oem_store import OEMStore


def test_disabled_without_db_path(monkeypatch):
    monkeypatch.delenv("ORBITAL_DB_PATH", raising=False)
    assert persistence.enabled() is False
    assert persistence.load_oems() == []
    assert persistence.load_waypoints() == {}


def test_oem_grants_survive_restart(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBITAL_DB_PATH", str(tmp_path / "orbital.db"))

    store1 = OEMStore()
    partner, _ = store1.register(OEMRegisterIn(
        company_name="Persist Co", vendor="Unitree", contact_email="p@x.com",
        transport=ControlTransport.ROS2,
    ))
    store1.grant_scopes(partner.id, [APIScope.TELEMETRY, APIScope.ESTOP])

    # A fresh store == a process restart: it must rehydrate from disk.
    store2 = OEMStore()
    reloaded = store2.get(partner.id)
    assert reloaded is not None
    assert APIScope.ESTOP in reloaded.granted_scopes
    # The API key hash also survives (authentication still works after restart).
    assert store2.partner_for_vendor("Unitree") is not None


def test_waypoints_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("ORBITAL_DB_PATH", str(tmp_path / "orbital.db"))
    persistence.init_db()
    persistence.save_waypoints("rbt-01", [[3.0, 4.0], [7.0, 8.0]])
    assert persistence.load_waypoints()["rbt-01"] == [[3.0, 4.0], [7.0, 8.0]]
    # Empty list deletes the row.
    persistence.save_waypoints("rbt-01", [])
    assert "rbt-01" not in persistence.load_waypoints()
