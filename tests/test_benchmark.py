"""Benchmark Library math: drift stats, MTBD, recovery latency, env score."""
import time

from orbital_cloud.benchmark import render_benchmark_report
from orbital_cloud.models import TelemetryIn
from orbital_cloud.store import Store, _p95


def _tele(store, robot_id, delta, ts):
    r = store.robots[robot_id]
    store.ingest_telemetry(
        TelemetryIn(robot_id=robot_id, vendor=r.vendor, model=r.model, facility_id="f", delta_meters=delta, ts=ts)
    )


def test_degradation_event_and_recovery_latency():
    store = Store()
    rid = next(iter(store.robots))
    t0 = time.time()
    # clean -> degraded (event opens) -> clean (recovery recorded)
    _tele(store, rid, 0.02, t0)
    _tele(store, rid, 0.30, t0 + 1)     # crosses 0.1 -> degradation event #1
    _tele(store, rid, 0.40, t0 + 2)     # still degraded, no new event
    _tele(store, rid, 0.05, t0 + 3)     # recovered -> latency = 2s

    robot = store.robots[rid]
    assert robot.degradation_events == 1
    assert list(robot.recovery_latencies) == [2.0]

    detail = store.robot_detail(rid)
    assert detail.recovery_latency_seconds == 2.0
    # env score = mean(delta)/baseline(0.1); mean of [.02,.3,.4,.05]=0.1925 -> 1.925
    assert detail.env_degradation_score == round((0.02 + 0.30 + 0.40 + 0.05) / 4 / 0.1, 3)


def test_vendor_benchmark_aggregates():
    store = Store()
    unitree = [r.id for r in store.robots.values() if r.vendor == "Unitree"]
    assert unitree, "seed fleet should include Unitree"
    t0 = time.time()
    for rid in unitree:
        _tele(store, rid, 0.10, t0)
        _tele(store, rid, 0.20, t0 + 1)

    bench = store.benchmark("Unitree")
    assert bench.vendor == "Unitree"
    assert bench.robots == len(unitree)
    assert bench.samples == 2 * len(unitree)
    assert bench.mean_drift_m == 0.15
    assert bench.p95_drift_m == 0.2


def test_p95_helper():
    assert _p95([]) == 0.0
    assert _p95([0.1]) == 0.1
    assert _p95([0.1, 0.2, 0.3, 0.4, 0.5]) == 0.5


def test_report_renders_all_vendors():
    store = Store()
    report = render_benchmark_report(store)
    assert "Benchmark Library" in report
    assert "Unitree" in report
