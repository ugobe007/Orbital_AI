"""Sprint J3/J4 — tracked objects: ingest, expiry, map, MCP."""
import math

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
    assert {"forklift-1", "forklift-2", "cart-1"} <= set(first)
    assert any(o.label == "pallet" for o in first.values())
    assert later["forklift-1"].x != first["forklift-1"].x
    for _ in range(200):
        for o in sim.batch(0.5).objects:
            if o.label == "truck":
                assert o.y > 16                       # out in the yard, never on the floor
            else:
                assert 0 <= o.x <= 24 and 0 <= o.y <= 16.5


def test_forklifts_carry_pallets_to_the_dock_and_trucks_take_them():
    sim = SimObjects()
    seen_activity, picked_from, delivered, truck_y = set(), set(), {}, []
    for _ in range(1200):
        for o in sim.batch(0.25).objects:
            if o.label == "forklift":
                seen_activity.add(o.activity.split(" ")[0])
            # A pallet set down while a truck is docked can go straight onto it.
            if o.label == "pallet" and o.activity.startswith(("Delivered", "Going onto")):
                delivered.setdefault(o.id, o)
            if o.label == "truck":
                truck_y.append(o.y)
        picked_from |= {f.pick[0] for f in sim.forklifts if f.pick}
    assert {"Heading", "Lifting", "Carrying", "Setting"} <= seen_activity
    assert len(picked_from) >= 3
    assert len(delivered) >= 5
    assert all(o.y > 14 and 12 <= o.x <= 18 for o in delivered.values())
    # Trucks back up to the dock door, load what's waiting and leave, so the dock never jams.
    assert sim.trucks_loaded >= 3 and sim.shipped >= 10
    assert min(truck_y) == pytest.approx(16 + objects_mod.TRAILER_SIZE["length"] / 2)
    live = [o for o in sim.batch(0.25).objects if o.label == "pallet" and o.activity.startswith("Delivered")]
    assert len(live) <= 3


def test_forks_reach_the_pallet_they_pick():
    sim = SimObjects()
    for _ in range(400):
        sim.batch(0.25)
        for f in sim.forklifts:
            if f.phase == "lifting":
                p = sim.pallets[f.pallet]
                tx, ty = f.fork_tip
                assert abs(tx - p["x"]) < 0.05 and abs(ty - p["y"]) < 0.05


def test_forklift_footprints_never_touch():
    """Forks included: the two forklifts never touch, and they still get the work done."""
    sim = SimObjects(seed=3)
    closest = 99.0
    for _ in range(2400):
        sim.batch(0.25)
        a, b = sim.forklifts
        closest = min(closest, a.box.gap(b.box))
    assert closest > 0.2
    assert sim.shipped > 0


def test_robots_slow_down_near_a_tracked_forklift():
    assert objects_mod.vehicle_speed_cap(18.0, 8.1) is None
    object_store.ingest(FAC, ObjectsIn(source="cv", objects=[FORKLIFT]))
    assert objects_mod.vehicle_speed_cap(19.0, 8.1) == settings.human_slow_speed_mps
    assert objects_mod.vehicle_speed_cap(5.0, 3.0) is None


def test_object_activity_is_optional_and_shown_on_the_map():
    obj = {**PALLET, "activity": "On the forks"}
    client.post(f"/api/v1/objects/{FAC}", json={"source": "wms", "objects": [obj, FORKLIFT]})
    by_id = {o["id"]: o for o in client.get(f"/api/v1/objects/{FAC}").json()["objects"]}
    assert by_id["pallet-7"]["activity"] == "On the forks"
    assert by_id["fl-1"]["activity"] is None


def test_mcp_get_map_describes_objects():
    from orbital_mcp.server import build_server

    class _Client:
        def get_map(self):
            return {"objects": []}

    server = build_server(client_factory=_Client)
    tool = server._tool_manager.get_tool("get_map")
    assert "objects" in tool.description and "forklift" in tool.description


def test_carried_pallet_stays_on_the_forks_while_yielding():
    sim = SimObjects(seed=3)
    checked = 0
    for _ in range(2400):
        sim.batch(0.25)
        for f in sim.forklifts:
            if f.pallet and sim.pallets[f.pallet]["state"] == "carried":
                p, (tx, ty) = sim.pallets[f.pallet], f.fork_tip
                assert abs(p["x"] - tx) < 1e-9 and abs(p["y"] - ty) < 1e-9
                checked += f.blocked_s > 0 or f.reverse_m > 0
    assert checked > 0          # the run did exercise a carrying forklift stopping or backing up


def test_a_forklift_holding_a_pallet_keeps_its_rack_face():
    from orbital_cloud.objects import PICK_FACES

    sim = SimObjects()
    a, b = sim.forklifts
    sim._assign(a)
    a.phase = "holding"
    for _ in range(50):
        b.phase = "idle"
        sim._assign(b)
        assert b.pick[0] != a.pick[0]
    assert len(PICK_FACES) > 1


def test_box_gap_measures_clearance_between_footprints():
    from orbital_cloud.objects import Box

    a = Box(0.0, 0.0, 0.0, 1.0, 0.5)
    assert a.gap(Box(3.0, 0.0, 0.0, 1.0, 0.5)) == pytest.approx(1.0)
    assert a.gap(Box(0.0, 2.0, 0.0, 1.0, 0.5)) == pytest.approx(1.0)
    assert a.gap(Box(1.5, 0.0, 0.7, 1.0, 0.5)) == 0.0                 # overlapping
    assert a.dist(0.2, 0.1) == 0.0 and a.dist(2.0, 0.0) == pytest.approx(1.0)


@pytest.mark.parametrize("spot", [(15.4, 9.5), (15.4, 7.0), (13.6, 11.0), (13.4, 12.6)])
def test_forklifts_never_come_within_stop_distance_of_someone_standing_in_the_way(spot):
    """A person who won't move: forklifts stop short (forks included), go around or pick
    elsewhere, and keep shipping."""
    sim = SimObjects(seed=3)
    closest = 99.0
    for _ in range(2400):
        sim.batch(0.25, people=[("wkr-x", *spot)])
        closest = min(closest, *(f.box.dist(*spot) for f in sim.forklifts))
    assert closest >= objects_mod.PERSON_STOP_M - 1e-6
    assert sim.shipped >= 5


def test_a_stopped_forklift_says_why():
    sim = SimObjects(seed=3)
    seen = set()
    for _ in range(2400):
        for o in sim.batch(0.25, people=[("wkr-x", 15.4, 9.5)]).objects:
            if o.label == "forklift":
                seen.add(o.activity)
    assert "Stopped: a person is in the way" in seen


def test_the_cart_stops_for_a_person_in_the_back_aisle():
    sim = SimObjects(seed=3)
    for _ in range(800):
        sim.batch(0.25, people=[("wkr-x", 8.0, objects_mod.CART_Y)])
        assert abs(sim.cart_x - 8.0) > 1.0
    cart = next(o for o in sim.batch(0.25, people=[("wkr-x", 8.0, objects_mod.CART_Y)]).objects if o.id == "cart-1")
    assert cart.vx == 0.0 and cart.activity.startswith("Stopped")


def test_people_and_forklifts_share_the_floor_safely():
    """The sim crowd walking among the forklifts: nobody is ever inside a forklift's stop
    distance, the forklifts never touch, people never walk through a rack, and pallets ship."""
    import random

    from orbital_cloud.workers import SimCrowd, worker_store

    random.seed(5)
    worker_store.clear()
    crowd, sim = SimCrowd(), SimObjects(seed=5)
    crowd.spawn(8)
    now, dt = 1_000_000.0, 0.2
    for w in crowd._walkers:
        w.dwell_until = now + 2
    closest, gap = 99.0, 99.0
    try:
        for _ in range(int(600 / dt)):
            now += dt
            crowd.tick(dt, now=now, horns=sim.horns())
            people = [(w.id, w.pose.x, w.pose.y) for w in worker_store.list(now)]
            object_store.ingest(FAC, sim.batch(dt, people=people), now=now)
            a, b = sim.forklifts
            gap = min(gap, a.box.gap(b.box))
            for _, x, y in people:
                closest = min(closest, a.box.dist(x, y), b.box.dist(x, y))
                assert not any(r["x"] < x < r["x"] + r["w"] and r["y"] < y < r["y"] + r["h"]
                               for r in objects_mod.WAREHOUSE["racks"])
    finally:
        worker_store.clear()
    assert closest >= objects_mod.PERSON_STOP_M - 1e-6
    assert gap > 0.2
    assert sim.shipped >= 10


def test_people_step_out_of_a_honking_forklifts_way():
    from orbital_cloud.models import WorkerPoseIn
    from orbital_cloud.objects import forklift_box
    from orbital_cloud.workers import SimCrowd, worker_store

    worker_store.clear()
    crowd = SimCrowd()
    crowd.spawn(1)
    w = crowd._walkers[0]
    now = 1_000_000.0
    w.dwell_until = now + 60                       # busy at a station...
    x0, y0 = 11.3, 3.4                             # ...against rack C, in front of the forks
    worker_store.upsert(w.id, WorkerPoseIn(x=x0, y=y0), source="sim", now=now)
    horn = forklift_box(12.85, 4.0, -2.03, 1.5, 0.85)
    object_store.ingest(FAC, ObjectsIn(source="sim", objects=[
        {"id": "forklift-2", "label": "forklift", "x": 12.85, "y": 4.0, "yaw": -2.03,
         "size": objects_mod.FORKLIFT_SIZE}]), now=now)
    try:
        for _ in range(60):
            now += 0.2
            crowd.tick(0.2, now=now, horns={w.id: horn})
            p = worker_store.get(w.id, now).pose
            assert horn.dist(p.x, p.y) >= 0.69
        assert math.hypot(p.x - horn.cx, p.y - horn.cy) >= 2.5     # clear of the forks' swing
    finally:
        worker_store.clear()
