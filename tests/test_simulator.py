"""Simulator rich-telemetry synthesis + demo OEM seeding."""
from orbital_cloud import simulator
from orbital_cloud.models import APIScope, ControlTransport, OEMStatus, TelemetryIn
from orbital_cloud.oem_store import OEMStore
from orbital_cloud.store import Store


def test_sensors_payload_is_valid_telemetry():
    store = Store()
    robot = next(iter(store.robots.values()))
    payload = simulator._sensors(robot)
    # Builds a valid TelemetryIn with every channel populated.
    t = TelemetryIn(robot_id=robot.id, vendor=robot.vendor, model=robot.model,
                    facility_id="f", delta_meters=robot.drift_delta_m, **payload)
    assert t.battery and t.battery.pct is not None and t.battery.temperature_c is not None
    assert len(t.motors) == 4 and all(m.temperature_c is not None for m in t.motors)
    assert t.imu and len(t.imu.accel) == 3
    assert t.spatial is not None
    assert "cpu" in t.temperatures_c and "ambient" in t.temperatures_c


def test_sensor_spatial_mirrors_internal_pose_keeps_drift():
    store = Store()
    robot = next(iter(store.robots.values()))
    robot.pose_internal = robot.pose_external.model_copy(update={"x": robot.pose_external.x + 0.3})
    robot.drift_delta_m = 0.3
    t = TelemetryIn(robot_id=robot.id, vendor=robot.vendor, model=robot.model,
                    facility_id="f", delta_meters=robot.drift_delta_m, **simulator._sensors(robot))
    # Spatial reflects the self-reported (internal) pose, so ingest won't erase the drift.
    assert abs(t.spatial.x - robot.pose_internal.x) < 1e-6
    store.ingest_telemetry(t)
    assert store.robots[robot.id].drift_delta_m == 0.3


def test_prime_sets_a_sequence_and_missions():
    simulator.prime()
    seq = simulator.store.sequence
    assert seq["theme"] in {t["id"] for t in simulator.SEQUENCE_THEMES}
    goals = [r.mission_goal for r in simulator.store.robots.values() if r.mission_goal]
    assert goals and all("→" in g for g in goals)  # "Verb: A → B"


def test_new_sequence_rotates_theme_and_wakes_controllable_robots():
    simulator.prime()
    first = simulator.store.sequence["theme"]
    first_id = simulator.store.sequence["id"]
    simulator._new_sequence()
    assert simulator.store.sequence["theme"] != first          # avoids an immediate repeat
    assert simulator.store.sequence["id"] == first_id + 1
    for r in simulator.store.robots.values():
        if r.error_code == "E_STOP" or r.state.name == "CHARGING":
            continue
        assert r.mission_goal is not None                       # nothing left stale/idle


def test_mission_lifecycle_pickup_then_work_then_carry():
    simulator.prime()
    r = next(iter(simulator.store.robots.values()))
    simulator._assign_task(r)
    assert r.mission_phase == "en_route_pickup" and r.task_target is not None
    simulator._begin_work(r)
    assert r.mission_phase == "working" and r.task_target is None and r.work_until is not None
    simulator._begin_carry(r)
    assert r.mission_phase == "carrying"
    assert r.task_target == (r.mission_dropoff[1], r.mission_dropoff[2])


def test_sequence_public_reports_countdown_and_assignments():
    simulator.prime()
    pub = simulator.store.sequence_public()
    assert pub["label"] and pub["objective"] and pub["ends_in_s"] >= 0
    assert any(a["goal"] for a in pub["assignments"])


def test_seed_demo_is_idempotent_and_respects_ceiling():
    store = OEMStore()
    specs = [
        ("Unitree (demo)", "Unitree", ControlTransport.ROS2,
         [APIScope.TELEMETRY, APIScope.VELOCITY, APIScope.ESTOP, APIScope.MISSION]),
        ("Boston Dynamics (demo)", "Boston Dynamics", ControlTransport.GRPC,
         [APIScope.TELEMETRY, APIScope.VELOCITY, APIScope.ESTOP]),  # velocity NOT in BD ceiling
    ]
    store.seed_demo(specs)
    store.seed_demo(specs)  # second pass must not duplicate

    assert len(store.list()) == 2
    uni = store.partner_for_vendor("Unitree")
    assert uni and APIScope.VELOCITY in uni.granted_scopes and uni.status == OEMStatus.ACTIVE

    bd = store.partner_for_vendor("Boston Dynamics")
    assert bd and APIScope.VELOCITY not in bd.granted_scopes  # dropped — outside gRPC ceiling
    assert APIScope.ESTOP in bd.granted_scopes
