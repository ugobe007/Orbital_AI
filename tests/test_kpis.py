"""Live-floor value bar: running totals."""
from fastapi.testclient import TestClient

from orbital_cloud.kpis import FloorKpis
from orbital_cloud.main import app


def test_slowdowns_count_each_encounter_once():
    k = FloorKpis()
    for t, slowed in [(0, True), (1, True), (2, False), (3, True), (4, False)]:
        k.note_slowed("rbt-01", slowed, now=t)          # flickering at the edge: one encounter
    k.note_slowed("rbt-01", True, now=30)               # a new encounter later
    k.note_slowed("rbt-02", True, now=0)
    assert k.snapshot()["slowdowns"] == 3


def test_fleet_busy_share():
    k = FloorKpis()
    assert k.snapshot()["fleet_busy_pct"] is None
    k.note_time(3.0, working=True)
    k.note_time(1.0, working=False)
    assert k.snapshot()["fleet_busy_pct"] == 75


def test_fleet_endpoint_carries_kpis():
    body = TestClient(app).get("/api/dashboard/fleet").json()
    assert {"deliveries", "slowdowns", "safety_stops", "pallets_shipped", "trucks_loaded",
            "fleet_busy_pct", "since"} <= set(body["kpis"])
