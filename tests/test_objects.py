"""Sprint J3/J4 — tracked objects: ingest, expiry, map, MCP."""
import pytest
from fastapi.testclient import TestClient

from orbital_cloud import objects as objects_mod
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.models import ObjectsIn
from orbital_cloud.objects import ObjectStore, ObjectStoreFull, SimObjects, object_store

client = TestClient(app)
FAC = settings.facility_id

PALLET = {"id": "pallet-7", "label": "pallet", "x": 13.9, "y": 12.2, "yaw": 0.3,
          "size": {"length": 1.2, "width": 1.0, "height": 1.1}, "confidence": 0.9}
FORKLIFT = {"id": "fl-1", "label": "forklift", "x": 18.0, "y": 8.1, "vx": 0.8,
            "size": {"length": 2.2, "width": 1.1}}


@pytest.fixture(autouse=True)
def _clean():
    object_store.clear()
    yield
    object_store.clear()


def test_ingest_lists_objects_on_both_maps():
    resp = client.post(f"/api/v1/objects/{FAC}", json={"source": "partner-cv", "objects": [PALLET, FORKLIFT]})
    assert resp.status_code == 202
    assert resp.json() == {"ok": True, "accepted": 2, "live": 2}

    edge = client.get(f"/api/v1/objects/{FAC}").json()["objects"]
    assert [(o["label"], o["source"]) for o in edge] == [("forklift", "partner-cv"), ("pallet", "partner-cv")]
    pallet = next(o for o in edge if o["id"] == "pallet-7")
    assert pallet["yaw"] == pytest.approx(0.3)
    assert pallet["size"]["length"] == 1.2
    assert pallet["expires_at"] == pytest.approx(pallet["updated_at"] + settings.object_ttl_s)

    assert len(client.get("/api/dashboard/map").json()["objects"]) == 2
    assert len(client.get(f"/api/v1/map/{FAC}").json()["objects"]) == 2
    dash = client.get("/api/dashboard/objects").json()
    assert dash["type"] == "objects" and len(dash["objects"]) == 2


def test_objects_expire_and_reports_keep_them_alive():
    store = ObjectStore()
    store.ingest(FAC, ObjectsIn(objects=[PALLET, {**FORKLIFT, "ttl_s": 2}]), now=100.0)
    assert len(store.list(FAC, now=101.0)) == 2
    assert [o.id for o in store.list(FAC, now=103.0)] == ["pallet-7"]

    store.ingest(FAC, ObjectsIn(objects=[PALLET]), now=108.0)
    alive = store.list(FAC, now=115.0)
    assert [o.id for o in alive] == ["pallet-7"]
    assert alive[0].first_seen == 100.0 and alive[0].updated_at == 108.0
    assert store.list(FAC, now=118.5) == []


def test_replace_drops_only_that_sources_missing_objects():
    store = ObjectStore()
    store.ingest(FAC, ObjectsIn(source="cam-a", objects=[PALLET, FORKLIFT]), now=1.0)
    store.ingest(FAC, ObjectsIn(source="cam-b", objects=[{**PALLET, "id": "box-1", "label": "box"}]), now=1.0)
    store.ingest(FAC, ObjectsIn(source="cam-a", objects=[FORKLIFT], replace=True), now=2.0)
    assert sorted((o.source, o.id) for o in store.list(FAC, now=2.0)) == [("cam-a", "fl-1"), ("cam-b", "box-1")]


def test_same_id_from_two_sources_are_separate_objects():
    store = ObjectStore()
    store.ingest(FAC, ObjectsIn(source="a", objects=[PALLET]), now=1.0)
    store.ingest(FAC, ObjectsIn(source="b", objects=[{**PALLET, "x": 1.0}]), now=1.0)
    assert len(store.list(FAC, now=1.0)) == 2


def test_validation_rejects_bad_input():
    bad = [
        {"objects": [{**PALLET, "confidence": 1.5}]},
        {"objects": [{**PALLET, "id": "has space"}]},
        {"objects": [{**PALLET, "size": {"length": -1}}]},
        {"objects": [PALLET] * 201},
        {"source": "x" * 41, "objects": [PALLET]},
    ]
    for body in bad:
        assert client.post(f"/api/v1/objects/{FAC}", json=body).status_code == 422, body
    assert client.post("/api/v1/objects/bad id!", json={"objects": [PALLET]}).status_code == 422


def test_facility_cap(monkeypatch):
    monkeypatch.setattr(objects_mod, "_MAX_FACILITIES", 2)
    store = ObjectStore()
    store.ingest("f1", ObjectsIn(objects=[PALLET]), now=1.0)
    store.ingest("f2", ObjectsIn(objects=[{**PALLET, "ttl_s": 1}]), now=1.0)
    with pytest.raises(ObjectStoreFull):
        store.ingest("f3", ObjectsIn(objects=[PALLET]), now=1.5)
    store.ingest("f3", ObjectsIn(objects=[PALLET]), now=5.0)   # f2 has expired, so it frees a slot
    assert store.list("f3", now=5.0)


def test_sim_objects_move_and_stay_on_the_floor():
    sim = SimObjects()
    first = {o.id: o for o in sim.batch(0.2).objects}
    later = {o.id: o for o in sim.batch(3.0).objects}
    assert set(first) == {"pallet-1", "forklift-1", "cart-1"}
    assert later["forklift-1"].x != first["forklift-1"].x
    assert later["pallet-1"].x == first["pallet-1"].x
    for _ in range(200):
        for o in sim.batch(0.5).objects:
            assert 0 <= o.x <= 24 and 0 <= o.y <= 16


def test_mcp_get_map_describes_objects():
    from orbital_mcp.server import build_server

    class _Client:
        def get_map(self):
            return {"objects": []}

    server = build_server(client_factory=_Client)
    tool = server._tool_manager.get_tool("get_map")
    assert "objects" in tool.description and "forklift" in tool.description
