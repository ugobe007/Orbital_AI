"""Sprint J1–J2 — multi-view triangulation, voxel occupancy, and the planner routing around
what the cameras see."""
import json
import math
import random
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aria_edge.edge_agent import CloudSync
from aria_edge.multiview import (
    Camera,
    OccupancyVolume,
    TriangulationError,
    load_rig,
    triangulate,
    triangulate_tracks,
)
from orbital_cloud import pathing
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.models import CameraOccupancyIn
from orbital_cloud.occupancy import CameraOccupancy, camera_occupancy, synthetic_occupancy_grid
from scripts import map_from_cameras

client = TestClient(app)
FAC = settings.facility_id
DATA = Path(__file__).resolve().parents[1] / "testdata" / "multiview"
RIG = load_rig(DATA / "rig_8cam.json")

# A pallet parked in the corridor between racks C and G, right on the straight line from
# (13.5, 2) to (13.5, 13).
PALLET = (12.9, 7.0, 14.1, 8.0, 1.1)   # x0, y0, x1, y1, height


def _span(lo: float, hi: float, step: float) -> list[float]:
    # Inset 2 cm so samples never sit exactly on a voxel boundary.
    lo, hi = lo + 0.02, hi - 0.02
    n = math.ceil((hi - lo) / step)
    return [round(lo + (hi - lo) * i / n, 4) for i in range(n + 1)]


def pallet_points(step: float = 0.2) -> list[tuple[float, float, float]]:
    """Surface samples of the pallet, about as dense as the edge's feature matcher yields."""
    x0, y0, x1, y1, h = PALLET
    xs, ys = _span(x0, x1, step), _span(y0, y1, step)
    x0, x1, y0, y1, h = xs[0], xs[-1], ys[0], ys[-1], h - 0.02
    zs = [0.15, h / 2, h]
    pts = {(x, y, h) for x in xs for y in ys}
    pts |= {(x, y, z) for z in zs for x in xs for y in (y0, y1)}
    pts |= {(x, y, z) for z in zs for y in ys for x in (x0, x1)}
    return sorted(pts)


def pallet_frames(n: int = 2, noise_px: float = 0.3, seed: int = 7, bump: dict | None = None) -> dict:
    """Feature tracks for ``n`` frames, as the edge's matcher would emit them. ``bump`` shifts
    one camera's pixels (camera id -> (du, dv)) as if it had been knocked."""
    rnd = random.Random(seed)
    frames = []
    for f in range(n):
        tracks = {}
        for k, p in enumerate(pallet_points()):
            sightings = []
            for cam in RIG.values():
                if not cam.sees(p):
                    continue
                u, v = cam.project(p)
                du, dv = (bump or {}).get(cam.id, (0.0, 0.0))
                sightings.append([cam.id, round(u + du + rnd.gauss(0, noise_px), 3),
                                  round(v + dv + rnd.gauss(0, noise_px), 3)])
            tracks[f"p{k}"] = sightings
        frames.append({"t": 100.0 + 0.2 * f, "tracks": tracks})
    return {"frames": frames}


@pytest.fixture(autouse=True)
def _clean():
    camera_occupancy.clear()
    yield
    camera_occupancy.clear()


# ── J1: triangulation ─────────────────────────────────────────────────────────

def test_eight_camera_rig_recovers_points_within_5mm():
    rnd = random.Random(1)
    exact, noisy = [], []
    for _ in range(150):
        p = (rnd.uniform(11.5, 15.5), rnd.uniform(5.0, 10.0), rnd.uniform(0.1, 2.0))
        views = [(c, c.project(p)) for c in RIG.values() if c.sees(p)]
        assert len(views) >= 6
        exact.append(math.dist(triangulate(views).point, p))
        jittered = [(c, (u + rnd.gauss(0, 0.3), v + rnd.gauss(0, 0.3))) for c, (u, v) in views]
        noisy.append(math.dist(triangulate(jittered).point, p))
    assert max(exact) < 1e-6
    assert max(noisy) < 0.005
    assert sum(noisy) / len(noisy) < 0.002


def test_two_views_are_enough_and_overhead_cameras_work():
    down = Camera.look_at("overhead", (13.5, 7.5, 5.0), (13.5, 7.5, 0.0), fx=1000)
    side = RIG["dn-1"]
    p = (13.2, 7.9, 0.8)
    tri = triangulate([(down, down.project(p)), (side, side.project(p))])
    assert math.dist(tri.point, p) < 1e-6 and tri.views == 2
    assert tri.rms_px < 1e-6


def test_per_view_error_points_at_a_knocked_camera():
    p = (13.8, 7.2, 0.6)
    views = [(c, c.project(p)) for c in RIG.values()]
    views = [(c, (u + 25.0, v) if c.id == "dn-3" else (u, v)) for c, (u, v) in views]
    tri = triangulate(views)
    assert tri.worst_camera == "dn-3"
    assert tri.errors_px["dn-3"] > 3 * max(e for cid, e in tri.errors_px.items() if cid != "dn-3")


def test_triangulate_rejects_bad_input():
    cam = RIG["dn-1"]
    uv = cam.project((13.5, 7.5, 0.5))
    with pytest.raises(TriangulationError):
        triangulate([(cam, uv)])
    with pytest.raises(TriangulationError):
        triangulate([(cam, uv), (cam, uv)])


def test_track_report_explains_rejections():
    p = (13.5, 7.5, 0.5)
    good = [[c.id, *c.project(p)] for c in RIG.values()]
    bumped = [[cid, u + (40.0 if cid == "dn-5" else 0.0), v] for cid, u, v in good]
    report = triangulate_tracks(RIG, {
        "ok": good,
        "one-view": good[:1],
        "unknown-cams": [["nope", 1.0, 1.0], ["nada", 2.0, 2.0]],
        "bumped": bumped,
    })
    assert [tid for tid, _ in report.points] == ["ok"]
    assert "fewer than two" in report.rejected["one-view"]
    assert "fewer than two" in report.rejected["unknown-cams"]
    assert "camera dn-5" in report.rejected["bumped"]


# ── J2: voxel volume ──────────────────────────────────────────────────────────

def test_volume_needs_two_frames_ignores_floor_and_ceiling_and_decays():
    vol = OccupancyVolume()
    box = [(5.05, 5.05, 0.5), (5.12, 5.08, 0.9)]
    vol.add_points(box + [(9.0, 9.0, 0.01), (9.0, 9.0, 3.5)], now=0.0)
    assert vol.slice_cells(now=0.0) == []                  # one frame is not enough
    vol.add_points(box, now=0.2)
    assert vol.slice_cells(now=0.2) == [(20, 20)]          # 5.05 m / 0.25 m
    assert vol.slice_cells(now=0.2 + 3 * vol.half_life_s) == []
    vol.add_points([(5.05, 5.05, 0.5)], now=50.0)
    assert vol.slice_cells(now=50.0) == []                 # old evidence has gone, not banked


def test_pallet_frames_slice_to_its_footprint():
    vol = OccupancyVolume()
    for frame in pallet_frames()["frames"]:
        tracks = {tid: [tuple(s) for s in sightings] for tid, sightings in frame["tracks"].items()}
        report = triangulate_tracks(RIG, tracks)
        assert not report.rejected
        vol.add_points([tri.point for _, tri in report.points], now=frame["t"])
    payload = vol.payload(now=100.2)
    xs = [i for i, _ in payload["cells"]]
    ys = [j for _, j in payload["cells"]]
    assert len(payload["cells"]) == 6 * 4                              # solid, no holes
    assert (min(xs), max(xs), min(ys), max(ys)) == (51, 56, 28, 31)   # 12.75..14.25 x 7.0..8.0 m
    assert payload["source"] == "digital-net" and payload["cell_m"] == 0.25


# ── cloud: ingest, maps, planner ──────────────────────────────────────────────

def _route_clearance(start, route, box=PALLET) -> float:
    x0, y0, x1, y1, _ = box
    pts = [start, *route]
    worst = float("inf")
    for a, b in zip(pts, pts[1:]):
        for s in range(41):
            t = s / 40
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            dx = max(x0 - x, 0.0, x - x1)
            dy = max(y0 - y, 0.0, y - y1)
            worst = min(worst, math.hypot(dx, dy))
    return worst


def test_planner_routes_around_a_triangulated_obstacle():
    start, goal = (13.5, 2.0), (13.5, 13.0)
    assert pathing.plan(start, goal) == [goal]
    assert _route_clearance(start, [goal]) == 0.0           # straight through the pallet

    payload, summary = map_from_cameras.run(DATA / "rig_8cam.json", DATA / "pallet_frames.json",
                                            max_rms_px=3.0, cell_m=0.25, ttl_s=3.0)
    assert summary["rejected"] == 0 and summary["blocked_cells"] > 0
    resp = client.post(f"/api/v1/occupancy/{FAC}", json=payload)
    assert resp.status_code == 202 and resp.json()["blocked_cells"] == summary["blocked_cells"]

    for vendor in (None, "Unitree", "AgiBot"):
        route = pathing.plan(start, goal, vendor=vendor)
        assert len(route) > 1 and route[-1] == goal
        assert _route_clearance(start, route) >= 0.3

    grid = client.get(f"/api/v1/map/{FAC}").json()
    assert grid["stats"]["camera_cells"] == summary["blocked_cells"]
    assert grid["stats"]["source"] == "synthetic_warehouse+digital_net"
    res = grid["resolution"]
    assert grid["data"][int(7.5 / res) * grid["width"] + int(13.5 / res)] == 100
    assert len(client.get("/api/dashboard/map").json()["camera_cells"]) == summary["blocked_cells"]

    camera_occupancy.clear()
    assert pathing.plan(start, goal) == [goal]


def test_goal_inside_a_seen_obstacle_stops_short():
    client.post(f"/api/v1/occupancy/{FAC}", json={"cells": [[i, j] for i in range(51, 57) for j in range(28, 33)]})
    route = pathing.plan((13.5, 2.0), (13.5, 7.5))
    end = route[-1]
    assert end != (13.5, 7.5)
    assert not (12.75 <= end[0] <= 14.25 and 7.0 <= end[1] <= 8.25)


def test_slices_replace_per_source_and_lapse():
    store = CameraOccupancy()
    store.ingest(FAC, CameraOccupancyIn(source="cell-a", cells=[(1, 1), (1, 2)], ttl_s=2), now=10.0)
    store.ingest(FAC, CameraOccupancyIn(source="cell-b", cells=[(5, 5)], ttl_s=5), now=10.0)
    v = store.version
    store.ingest(FAC, CameraOccupancyIn(source="cell-a", cells=[(1, 2)], ttl_s=2), now=11.0)
    assert store.version > v
    assert store.rects(FAC, now=11.0) == [(0.25, 0.5, 0.5, 0.75), (1.25, 1.25, 1.5, 1.5)]
    v = store.version
    store.ingest(FAC, CameraOccupancyIn(source="cell-a", cells=[(1, 2)], ttl_s=2), now=11.5)
    assert store.version == v                               # same cells: planner cache stays valid
    assert store.rects(FAC, now=13.6) == [(1.25, 1.25, 1.5, 1.5)]
    assert store.version > v                                # a lapse is a change too
    assert store.rects(FAC, now=15.1) == []


def test_cells_off_the_floor_are_dropped():
    store = CameraOccupancy()
    assert store.ingest(FAC, CameraOccupancyIn(cells=[(-1, 3), (3, 3), (500, 3), (3, 900)]), now=1.0) == 1


def test_occupancy_validation():
    bad = [
        {"cell_m": 0.01, "cells": [[1, 1]]},
        {"ttl_s": 0, "cells": [[1, 1]]},
        {"source": "has space", "cells": [[1, 1]]},
        {"cells": [[1, 1, 1]]},
        {"cells": [[1, 1]] * 20_001},
    ]
    for body in bad:
        assert client.post(f"/api/v1/occupancy/{FAC}", json=body).status_code == 422
    assert client.post("/api/v1/occupancy/bad id!", json={"cells": []}).status_code == 422
    assert client.get(f"/api/v1/occupancy/{FAC}").json()["cells"] == []


def test_synthetic_grid_unchanged_without_camera_cells():
    grid = synthetic_occupancy_grid(FAC)
    assert grid["stats"]["camera_cells"] == 0 and grid["stats"]["source"] == "synthetic_warehouse"


# ── edge client, CLI, MCP ─────────────────────────────────────────────────────

def test_cloud_sync_records_occupancy_offline():
    sync = CloudSync(enabled=False)
    sync.post_occupancy({"cells": [[1, 1]]})
    assert sync.occupancy_sent == [{"cells": [[1, 1]]}]


def test_cli_reports_and_flags_a_knocked_camera(tmp_path, capsys):
    out = tmp_path / "occ.json"
    assert map_from_cameras.main([str(DATA / "rig_8cam.json"), str(DATA / "pallet_frames.json"),
                                  "--out", str(out)]) == 0
    assert json.loads(out.read_text())["cells"]
    assert "floor cells blocked" in capsys.readouterr().out

    knocked = tmp_path / "knocked.json"
    knocked.write_text(json.dumps(pallet_frames(bump={"dn-4": (30.0, -20.0)})))
    assert map_from_cameras.main([str(DATA / "rig_8cam.json"), str(knocked)]) == 1
    assert "camera dn-4 was the outlier" in capsys.readouterr().out

    missing = tmp_path / "nope.json"
    assert map_from_cameras.main([str(DATA / "rig_8cam.json"), str(missing)]) == 2


def test_fixture_matches_generator():
    assert json.loads((DATA / "pallet_frames.json").read_text()) == pallet_frames()


def test_mcp_get_map_mentions_camera_cells():
    from orbital_mcp.server import build_server

    class _Client:
        def get_map(self):
            return {}

    tool = build_server(client_factory=_Client)._tool_manager.get_tool("get_map")
    assert "camera_cells" in tool.description
