"""scripts/capture_floor.py: recording the live floor to disk."""
import csv
import json

from scripts.capture_floor import FloorRecorder


def _robot(rid, x, y, state="active", drift=0.05):
    return {"id": rid, "vendor": "Unitree", "model": "G1", "state": state, "battery_pct": 90.0,
            "pose_external": {"x": x, "y": y, "theta": 0.0}, "pose_internal": {"x": x + drift, "y": y, "theta": 0.0},
            "drift_delta_m": drift, "current_task": "Patrol", "mission_phase": "carrying",
            "control_mode": "patrol", "error_code": None, "simulated": True}


def _kpis(shipped, deliveries):
    return {"since": 0, "pallets_shipped": shipped, "trucks_loaded": 0, "deliveries": deliveries,
            "slowdowns": 0, "safety_stops": 0, "fleet_busy_pct": 80}


def test_records_frames_on_a_steady_grid_and_writes_every_file(tmp_path):
    rec = FloorRecorder(tmp_path, hz=2.0, url="http://x", facility={"id": "f", "name": "Test floor"})
    rec.on_message({"type": "objects", "objects": [{"id": "forklift-1", "label": "forklift", "x": 1, "y": 2,
                                                    "activity": "Carrying a pallet to Dock lane 2"}]}, 0.0)
    for i in range(51):                                   # 10 s of a 5 Hz feed, 1 m/s along x
        t = i * 0.2
        rec.on_message({"type": "fleet", "robots": [_robot("rbt-01", t, 1.0)],
                        "workers": [{"id": "wkr-01", "pose": {"x": 3, "y": 3}, "status": "on_shift"}],
                        "kpis": _kpis(i // 25, i // 10)}, t)
    meta = rec.finish(10.0)
    assert meta["frames"] == 21                           # 2 Hz over 10 s, both ends included
    assert meta["simulated"] is True and meta["robots"] == ["rbt-01"]
    frames = [json.loads(line) for line in (tmp_path / "frames.jsonl").read_text().splitlines()]
    f = frames[-1]
    assert f["robots"][0]["x"] == 10.0 and f["robots"][0]["ix"] == 10.05
    assert f["objects"][0]["activity"] == "Carrying a pallet to Dock lane 2"
    assert f["workers"][0]["id"] == "wkr-01" and f["kpis"]["deliveries"] == 5
    rows = list(csv.reader((tmp_path / "kpis.csv").open()))
    assert rows[0][:3] == ["t", "pallets_shipped", "trucks_loaded"] and len(rows) == 22
    summary = (tmp_path / "summary.md").read_text()
    assert "SIMULATION" in summary and "Test floor" in summary
    assert "| rbt-01 | Unitree G1 | 10.0 |" in summary
    assert "| Pallets shipped | 2 | — |" in summary         # too short for an hourly rate
    assert json.loads((tmp_path / "meta.json").read_text())["kpis_end"]["deliveries"] == 5


def test_teleports_are_not_counted_as_distance(tmp_path):
    rec = FloorRecorder(tmp_path, hz=0)
    for t, x in [(0, 1.0), (1, 2.0), (2, 20.0), (3, 21.0)]:
        rec.on_message({"type": "fleet", "robots": [_robot("rbt-01", x, 1.0)]}, t)
    rec.finish(3)
    assert rec.distance["rbt-01"] == 2.0


def test_alerts_are_recorded_once_and_old_ones_are_skipped(tmp_path):
    rec = FloorRecorder(tmp_path)
    old = {"id": "alr-old", "type": "drift_exceeded", "robot_id": "rbt-01", "ts": 1}
    new = {"id": "alr-new", "type": "human_proximity", "robot_id": "rbt-02", "ts": 2}
    rec.mark_seen([old])
    rec.on_alert(old, 5)
    rec.on_message({"type": "alert", "alert": new}, 6)
    rec.on_alert(new, 7)                                  # a poll sees it again
    meta = rec.finish(8)
    lines = (tmp_path / "alerts.jsonl").read_text().splitlines()
    assert [json.loads(x)["id"] for x in lines] == ["alr-new"] and meta["alerts"] == 1
    assert "human_proximity: 1" in (tmp_path / "summary.md").read_text()


def test_no_frames_still_finishes_cleanly(tmp_path):
    meta = FloorRecorder(tmp_path).finish(1.0)
    assert meta["frames"] == 0 and (tmp_path / "summary.md").exists()
