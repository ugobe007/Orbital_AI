"""Fix #1 — lab results report: A/B numbers from recorded runs, honest labelling."""
import json

from fastapi.testclient import TestClient

from aria_edge.edge_agent import CloudSync, EdgeAgent, RobotBinding
from aria_edge.types import CameraFrame, Pose2D
from fleet_adapters import get_adapter
from orbital_cloud.main import app
from orbital_cloud.runs import run_store
from scripts import lab_report


def _run(source, err, n=11, halts=(), outcome="complete", dt=0.5):
    samples = [{"t": i * dt, "x": float(i), "y": 0.0, "theta": 0.0, "ix": float(i) + err, "iy": 0.0,
                "drift_m": err, "state": "halted" if i in halts else "active"} for i in range(n)]
    return {"robot_id": "rbt-01", "vendor": "Unitree", "task_key": "Aisle AB → Dock", "source": source,
            "started_at": 0.0, "ended_at": samples[-1]["t"], "outcome": outcome, "samples": samples}


def test_ab_report_numbers():
    runs = ([_run("lab-baseline", 0.30) for _ in range(9)] + [_run("lab-baseline", 0.30, outcome="failed")]
            + [_run("lab-orbital", 0.03, dt=0.55) for _ in range(10)])
    rep = lab_report.build_report(runs, baseline="lab-baseline", orbital="lab-orbital", min_runs=9)
    assert rep["baseline"]["complete"] == 9 and rep["baseline"]["completion_rate"] == 0.9
    assert abs(rep["baseline"]["error_mean_m"] - 0.30) < 1e-9
    assert abs(rep["improvement"]["error_mean"] - 0.9) < 1e-9
    assert abs(rep["improvement"]["end_error_mean"] - 0.9) < 1e-9
    assert abs(rep["improvement"]["cycle_time_ratio"] - 1.1) < 1e-9
    assert rep["enough_runs"] and not rep["simulated"]

    md = lab_report.render_markdown(rep)
    assert "cut mean position error by 90% (30.0 cm → 3.0 cm)" in md
    assert "| Position error, mean | 30.0 cm | 3.0 cm | -90% |" in md
    assert "SIMULATION" not in md and "Not enough runs" not in md


def test_simulated_or_thin_data_is_never_presented_as_lab_results(tmp_path):
    sim = [_run("sim", 0.2) for _ in range(12)]
    rep = lab_report.build_report(sim, baseline="sim", orbital="sim", min_runs=10)
    assert rep["simulated"]
    assert "SIMULATION — not lab results" in lab_report.render_markdown(rep)

    thin = [_run("lab-baseline", 0.3)] + [_run("lab-orbital", 0.03)]
    rep = lab_report.build_report(thin, baseline="lab-baseline", orbital="lab-orbital", min_runs=10)
    assert "Not enough runs to publish" in lab_report.render_markdown(rep)

    path = tmp_path / "runs.json"
    path.write_text(json.dumps(thin))
    assert lab_report.main(["--input", str(path)]) == 1


def test_worse_with_orbital_shows_a_positive_change():
    runs = [_run("lab-baseline", 0.05)] + [_run("lab-orbital", 0.10)]
    md = lab_report.render_markdown(lab_report.build_report(runs, baseline="lab-baseline",
                                                            orbital="lab-orbital", min_runs=1))
    assert "| Position error, mean | 5.0 cm | 10.0 cm | +100% |" in md


def test_halts_latency_and_calibration_sections(tmp_path):
    runs = ([_run("lab-baseline", 0.3, halts=(2, 3, 7)) for _ in range(2)]
            + [_run("lab-orbital", 0.03) for _ in range(2)])
    src = tmp_path / "runs.json"
    src.write_text(json.dumps(runs))
    bench = tmp_path / "bench.json"
    bench.write_text(json.dumps({"hz_target": 10, "samples": 30, "mean_ms": 41.0, "p95_ms": 72.5,
                                 "max_ms": 90.1, "budget_ms": 100, "pass": True}))
    calib = tmp_path / "cal.json"
    calib.write_text(json.dumps({"ok": True, "rms_m": 0.004, "markers": [{}] * 12, "captures": [{}] * 8}))
    out, raw = tmp_path / "results.md", tmp_path / "results.json"
    rc = lab_report.main(["--input", str(src), "--min-runs", "2", "--latency", str(bench),
                          "--calibration", str(calib), "--site", "SF lab", "--out", str(out), "--json", str(raw)])
    assert rc == 0
    md = out.read_text()
    assert md.startswith("# Lab results — SF lab")
    assert "| Safety halts per run | 2.00 | 0.00 |" in md
    assert "p95 72.5 ms" in md and "**pass**" in md
    assert "12 markers from 8 captures, RMS 0.4 cm — **clean**" in md
    assert json.loads(raw.read_text())["baseline"]["halts_per_run"] == 2.0

    assert lab_report.main(["--input", str(tmp_path / "nope.json")]) == 2


def _edge_run(correction: bool) -> tuple[dict, list[dict]]:
    adapter = get_adapter("Unitree", "rbt-01")
    cloud = CloudSync(enabled=False)
    agent = EdgeAgent([RobotBinding("rbt-01", "Unitree", "G1", adapter=adapter)], cloud=cloud)
    agent.correction_enabled = correction
    clock = [1000.0]
    agent.clock = lambda: clock[0]
    agent.start_run("rbt-01", "Aisle AB → Dock")
    results = []
    for i in range(10):
        clock[0] += 0.5
        truth = Pose2D(2.0 + 0.4 * i, 5.0)
        own = Pose2D(truth.x + 0.25, truth.y)            # SLAM thinks it is 25 cm further on
        frame = CameraFrame(camera_id="cam-0", ts=clock[0], width=640, height=480, ground_truth={"rbt-01": truth})
        results.append(agent.tick(frame, {"rbt-01": own})[0])
    payload = agent.finish_run("rbt-01")
    assert cloud.runs_sent == [payload]
    return payload, results


def test_edge_records_ab_runs_the_cloud_and_report_accept():
    baseline, base_results = _edge_run(correction=False)
    orbital, orb_results = _edge_run(correction=True)
    assert baseline["source"] == "lab-baseline" and orbital["source"] == "lab-orbital"
    assert {r["action"] for r in base_results} == {"observe_only"}
    assert "correct" in {r["action"] for r in orb_results}
    assert len(baseline["samples"]) == 10 and baseline["samples"][0]["ix"] - baseline["samples"][0]["x"] == 0.25
    assert baseline["ended_at"] - baseline["started_at"] == 5.0

    client = TestClient(app)
    run_store.clear()
    try:
        for payload in (baseline, orbital):
            assert client.post("/api/v1/runs/facility-sf-001", json=payload).status_code == 201
        stored = [r for r in run_store.list() if r.source.startswith("lab-")]
        assert len(stored) == 2
    finally:
        run_store.clear()

    rep = lab_report.build_report([baseline, orbital], baseline="lab-baseline", orbital="lab-orbital", min_runs=1)
    assert rep["baseline"]["complete"] == rep["orbital"]["complete"] == 1
    assert abs(rep["baseline"]["error_mean_m"] - 0.25) < 1e-6
