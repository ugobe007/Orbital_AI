"""Sprint K — recorded runs, Golden Run scoring, and dataset export."""
import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from orbital_cloud import simulator
from orbital_cloud.config import settings
from orbital_cloud.main import app
from orbital_cloud.models import RunIn
from orbital_cloud.runs import path_deviation, recorder, recoveries, run_store, score
from orbital_cloud.store import store
from scripts import export_dataset

client = TestClient(app)
FAC = settings.facility_id


def _samples(points, dt=0.5, halts=(), drift=0.02):
    """Straight-ish path through ``points`` with one sample per point; ``halts`` are sample
    indices where the robot is halted."""
    return [{"t": i * dt, "x": x, "y": y, "theta": 0.0, "ix": x + drift, "iy": y, "itheta": 0.0,
             "drift_m": drift, "speed_mps": 0.6, "state": "halted" if i in halts else "active"}
            for i, (x, y) in enumerate(points)]


LINE = [(2.0 + 0.5 * i, 5.0) for i in range(21)]   # 10 m east at y = 5


def _run(points=LINE, dt=0.5, halts=(), outcome="complete", task="Aisle AB → Dock", vendor="Unitree",
         robot="rbt-01"):
    samples = _samples(points, dt, halts)
    start = 1000.0
    return {"robot_id": robot, "vendor": vendor, "task_key": task, "started_at": start,
            "ended_at": start + samples[-1]["t"], "outcome": outcome, "samples": samples}


@pytest.fixture(autouse=True)
def _clean():
    run_store.clear()
    recorder.clear()
    yield
    run_store.clear()
    recorder.clear()


def test_scoring_pass_slow_deviated_unstable_failed():
    golden = run_store.add(RunIn(**_run()))
    assert score(RunIn(**_run()), golden).verdict == "pass"

    slow = score(RunIn(**_run(dt=0.7)), golden)
    assert slow.verdict == "slow" and slow.cycle_ratio == pytest.approx(1.4)
    assert "40% slower" in slow.reasons[0]

    detour = [(x, 5.0 + (1.2 if 8 <= i <= 12 else 0.0)) for i, (x, _) in enumerate(LINE)]
    dev = score(RunIn(**_run(points=detour)), golden)
    assert dev.verdict == "deviated" and dev.max_dev_m == pytest.approx(1.2, abs=0.01)

    shaky = score(RunIn(**_run(halts=(5, 6, 14))), golden)
    assert shaky.verdict == "unstable" and shaky.recoveries == 2

    assert score(RunIn(**_run(outcome="aborted")), golden).verdict == "failed"
    assert score(RunIn(**_run()), None).verdict == "no_golden"


def test_deviation_catches_a_shortcut_too():
    golden = [RunIn(**_run(points=[(0, 0), (0, 4), (4, 4)])).samples][0]
    cut = RunIn(**_run(points=[(0, 0), (2, 2), (4, 4)])).samples
    _, worst = path_deviation(cut, golden)
    assert worst > 1.0


def test_recoveries_count_entries_into_halt():
    s = RunIn(**_run(halts=(2, 3, 4, 9))).samples
    assert recoveries(s) == 2


def test_first_clean_edge_run_becomes_golden_and_later_runs_are_scored():
    first = client.post(f"/api/v1/runs/{FAC}", json=_run(halts=(3,)))
    assert first.status_code == 201
    assert first.json()["golden"] is False                    # a run with a halt is not a reference
    assert first.json()["score"]["verdict"] == "no_golden"

    clean = client.post(f"/api/v1/runs/{FAC}", json=_run()).json()
    assert clean["golden"] is True and clean["score"]["verdict"] == "golden"

    slow = client.post(f"/api/v1/runs/{FAC}", json=_run(dt=0.8)).json()
    assert slow["score"]["verdict"] == "slow" and slow["score"]["golden_id"] == clean["id"]

    other_vendor = client.post(f"/api/v1/runs/{FAC}", json=_run(dt=0.8, vendor="AgiBot")).json()
    assert other_vendor["golden"] is True                     # goldens are per (vendor, task)

    listed = client.get("/api/dashboard/runs").json()
    assert [r["id"] for r in listed][:2] == [other_vendor["id"], slow["id"]]
    assert {r["vendor"] for r in client.get("/api/dashboard/runs/golden").json()} == {"Unitree", "AgiBot"}
    assert len(client.get("/api/dashboard/runs", params={"vendor": "AgiBot"}).json()) == 1


def test_operator_can_replace_the_golden_run():
    client.post(f"/api/v1/runs/{FAC}", json=_run())
    faster = client.post(f"/api/v1/runs/{FAC}", json=_run(dt=0.35)).json()
    assert faster["score"]["verdict"] == "pass"

    resp = client.post(f"/api/dashboard/runs/{faster['id']}/golden")
    assert resp.status_code == 200 and resp.json()["golden"] is True
    detail = client.get(f"/api/dashboard/runs/{faster['id']}").json()
    assert detail["golden"] is None and len(detail["run"]["samples"]) == 21

    original = client.get("/api/dashboard/runs").json()[-1]
    assert original["score"]["verdict"] == "slow"             # now judged against the faster golden

    aborted = client.post(f"/api/v1/runs/{FAC}", json=_run(outcome="aborted")).json()
    assert client.post(f"/api/dashboard/runs/{aborted['id']}/golden").status_code == 409
    assert client.post("/api/dashboard/runs/run-nope/golden").status_code == 404
    assert client.get("/api/dashboard/runs/run-nope").status_code == 404


def test_run_validation():
    bad = _run()
    bad["samples"] = bad["samples"][::-1]
    assert client.post(f"/api/v1/runs/{FAC}", json=bad).status_code == 422
    one = _run()
    one["samples"] = one["samples"][:1]
    assert client.post(f"/api/v1/runs/{FAC}", json=one).status_code == 422
    assert client.post("/api/v1/runs/bad id!", json=_run()).status_code == 422


def test_simulator_records_station_to_station_legs(monkeypatch):
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    simulator.prime()

    async def go():
        for _ in range(1500):
            clock[0] += 0.2
            await simulator._tick(0.2)

    try:
        asyncio.run(go())
    finally:
        for robot in store.robots.values():
            robot.nav_queue = []
    runs = run_store.list(limit=500)
    assert len(runs) >= 20
    assert all(" → " in r.task_key and r.source == "sim" for r in runs)
    assert run_store.golden_runs()
    verdicts = {run_store.summary(r).score.verdict for r in runs}
    assert "pass" in verdicts and "golden" in verdicts
    sample = runs[0].samples
    assert sample[0].t == 0.0 and sample[-1].t > 1.0 and sample[0].ix is not None


def test_export_writes_lerobot_style_layout(tmp_path):
    client.post(f"/api/v1/runs/{FAC}", json=_run())
    client.post(f"/api/v1/runs/{FAC}", json=_run(dt=0.8))
    client.post(f"/api/v1/runs/{FAC}", json=_run(task="Dock → Bay G"))
    runs = []
    for s in client.get("/api/dashboard/runs").json():
        d = client.get(f"/api/dashboard/runs/{s['id']}").json()
        runs.append({**d["run"], "summary": d["summary"]})
    src = tmp_path / "runs.json"
    src.write_text(json.dumps(runs, ensure_ascii=False))
    out = tmp_path / "ds"
    assert export_dataset.main(["--input", str(src), "--out", str(out)]) == 0

    info = json.loads((out / "meta" / "info.json").read_text())
    assert info["total_episodes"] == 3 and info["total_frames"] == 63 and info["total_tasks"] == 2
    assert info["fps"] == pytest.approx(2.0)
    episodes = [json.loads(line) for line in (out / "meta" / "episodes.jsonl").read_text().splitlines()]
    assert [e["golden"] for e in episodes].count(True) == 2
    assert {e["verdict"] for e in episodes} >= {"golden", "slow"}
    tasks = [json.loads(line) for line in (out / "meta" / "tasks.jsonl").read_text().splitlines()]
    assert {t["task"] for t in tasks} == {"Aisle AB → Dock", "Dock → Bay G"}

    steps = [json.loads(line) for line in (out / "data" / "chunk-000" / "episode_000000.jsonl").read_text().splitlines()]
    assert len(steps) == 21
    assert steps[0]["observation.ground_truth"] == [2.0, 5.0, 0.0]
    assert steps[0]["observation.state"][0] == pytest.approx(2.02)
    assert steps[0]["action"][0] == pytest.approx(1.0)
    assert steps[-1]["next.done"] is True and steps[-1]["action"] == [0.0, 0.0, 0.0]

    assert export_dataset.main(["--input", str(src), "--out", str(tmp_path / "x"), "--task", "nope"]) == 1
    assert export_dataset.main(["--input", str(tmp_path / "missing.json"), "--out", str(tmp_path / "y")]) == 2
