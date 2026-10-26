"""Sprint H — camera-rig calibration: solver accuracy, fault localisation, CLI and cloud API."""
import copy
import json
import math
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aria_edge.calibration import (
    Capture,
    Observation,
    captures_from_frames,
    solve,
    solve_dict,
)
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.rig_calibration import calibration_store
from scripts import calibrate_rig

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "testdata" / "calibration"
client = TestClient(app)


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def _clean_store():
    calibration_store.clear()
    yield
    calibration_store.clear()


def test_good_rig_matches_truth_within_a_centimetre():
    data = _fixture("rig_good")
    report = solve_dict(data)
    assert report.ok
    assert report.hints == []
    for mid, pose in report.marker_map().items():
        t = data["truth"][str(mid)]
        assert math.hypot(pose.x - t["x"], pose.y - t["y"]) < 0.01, mid


def test_bumped_marker_is_flagged_by_id_and_size():
    report = solve_dict(_fixture("rig_bumped_marker"))
    assert not report.ok
    flagged = [m for m in report.markers if m.status != "ok"]
    assert [m.marker_id for m in flagged] == [9]
    assert flagged[0].max_m == pytest.approx(0.15, abs=0.02)
    assert all(c.status == "ok" for c in report.captures)
    assert any("Marker 9" in h and "15 cm" in h for h in report.hints)


def test_moved_camera_is_the_only_thing_flagged():
    report = solve_dict(_fixture("rig_moved_camera"))
    assert [c.id for c in report.captures if c.status != "ok"] == ["cam-03"]
    cam = next(c for c in report.captures if c.id == "cam-03")
    assert cam.status == "moved"
    assert cam.offset_m == pytest.approx(0.10, abs=0.01)
    assert all(m.status == "ok" for m in report.markers)


def test_disconnected_group_is_reported_unanchored():
    report = solve_dict(_fixture("rig_split"))
    assert sorted(m.marker_id for m in report.markers if m.status == "unanchored") == [20, 21]
    assert 20 not in report.marker_map()
    assert any("not linked to origin marker" in h for h in report.hints)


def test_single_view_marker_is_a_hint_not_a_failure():
    caps = [
        Capture("a", [Observation(0, 0.0, 0.0), Observation(1, 2.0, 0.0), Observation(2, 0.0, 2.0)]),
        Capture("b", [Observation(0, -1.0, 0.0), Observation(1, 1.0, 0.0), Observation(3, 1.0, 3.0)]),
    ]
    report = solve(caps, 0)
    statuses = {m.marker_id: m.status for m in report.markers}
    assert statuses[2] == statuses[3] == "single_view"
    assert report.ok
    assert any("seen in only one capture" in h for h in report.hints)


def test_frames_get_unique_ids_and_skip_files_without_markers(tmp_path):
    frame = json.loads((ROOT / "testdata" / "aruco" / "frame_001.json").read_text())
    (tmp_path / "a.json").write_text(json.dumps(frame))
    (tmp_path / "b.json").write_text(json.dumps(frame))
    (tmp_path / "map.json").write_text(json.dumps({"camera_id": "cam-0", "marker_map": []}))
    caps = captures_from_frames(sorted(tmp_path.glob("*.json")))
    assert [c.id for c in caps] == ["cam-0", "cam-0:b"]
    assert [o.marker_id for o in caps[0].observations] == [1, 2]


def test_cli_exit_codes(tmp_path, capsys):
    assert calibrate_rig.main([str(FIXTURES / "rig_good.json")]) == 0
    assert "Rig is clean." in capsys.readouterr().out

    out = tmp_path / "report.json"
    assert calibrate_rig.main([str(FIXTURES / "rig_bumped_marker.json"), "--out", str(out)]) == 1
    assert "off by 15" in capsys.readouterr().out
    assert json.loads(out.read_text())["ok"] is False

    assert calibrate_rig.main([str(tmp_path / "missing.json")]) == 2
    empty = tmp_path / "frames"
    empty.mkdir()
    assert calibrate_rig.main([str(empty)]) == 2


def test_cli_origin_pose_places_the_map(tmp_path, capsys):
    data = _fixture("rig_good")
    data.pop("origin_pose")
    data.pop("anchors")
    path = tmp_path / "rig.json"
    path.write_text(json.dumps(data))
    out = tmp_path / "report.json"
    origin = data["truth"]["0"]
    rc = calibrate_rig.main([str(path), "--origin-pose", f"{origin['x']}:{origin['y']}:{origin['theta']}",
                             "--anchor", "3:12.5:2.0", "--out", str(out)])
    assert rc == 0
    marker_3 = next(m for m in json.loads(out.read_text())["markers"] if m["marker_id"] == 3)
    assert marker_3["pose"]["x"] == pytest.approx(12.5, abs=0.01)


def test_api_round_trip():
    facility = settings.facility_id
    assert client.get(f"/api/v1/calibration/{facility}").status_code == 404
    assert client.get("/api/dashboard/calibration").json()["calibration"] is None

    resp = client.post(f"/api/v1/calibration/{facility}", json=_fixture("rig_bumped_marker"))
    assert resp.status_code == 201
    body = resp.json()
    assert body["facility_id"] == facility
    assert body["ok"] is False
    assert any(m["marker_id"] == 9 and m["status"] == "likely_error" for m in body["markers"])

    assert client.get(f"/api/v1/calibration/{facility}").json()["received_at"] == body["received_at"]
    assert client.get("/api/dashboard/calibration").json()["calibration"]["ok"] is False


def test_api_threshold_override_and_limits():
    data = _fixture("rig_bumped_marker")
    data["threshold_m"] = 0.5
    assert client.post("/api/v1/calibration/site-2", json=data).json()["ok"] is True

    too_many = copy.deepcopy(data)
    too_many["captures"] = [{**too_many["captures"][0], "id": f"c{i}"} for i in range(61)]
    assert client.post("/api/v1/calibration/site-2", json=too_many).status_code == 422

    dupes = copy.deepcopy(data)
    dupes["captures"][1]["id"] = dupes["captures"][0]["id"]
    assert client.post("/api/v1/calibration/site-2", json=dupes).status_code == 422

    assert client.post("/api/v1/calibration/bad id!", json=data).status_code == 422
