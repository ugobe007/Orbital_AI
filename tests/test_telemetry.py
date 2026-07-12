"""Rich multi-modal telemetry — battery, motors, IMU, spatial pose, temperatures."""
from fastapi.testclient import TestClient

from orbital_cloud.main import app
from orbital_cloud.models import (
    BatteryTelemetry,
    ImuTelemetry,
    MotorTelemetry,
    SpatialTelemetry,
    TelemetryIn,
)
from orbital_cloud.store import store

client = TestClient(app)


def _a_robot() -> str:
    return client.get("/api/dashboard/fleet").json()["robots"][0]["id"]


def test_minimal_telemetry_still_accepted():
    rid = _a_robot()
    r = client.post("/api/v1/telemetry", json={
        "robot_id": rid, "vendor": "Unitree", "model": "G1",
        "facility_id": "facility-sf-001", "delta_meters": 0.03,
    })
    assert r.status_code == 202


def test_rich_telemetry_ingest_and_surface():
    rid = _a_robot()
    payload = {
        "robot_id": rid, "vendor": "Unitree", "model": "G1",
        "facility_id": "facility-sf-001", "delta_meters": 0.04,
        "battery": {"pct": 73.5, "temperature_c": 34.2, "voltage_v": 48.1, "current_a": 6.2, "cycles": 210},
        "motors": [
            {"joint": "hip_left", "temperature_c": 52.0, "current_a": 3.1, "torque_nm": 12.4, "velocity_rad_s": 0.8},
            {"joint": "knee_left", "temperature_c": 61.5, "current_a": 4.0, "torque_nm": 18.0},
        ],
        "imu": {"accel": [0.1, -0.02, 9.79], "gyro": [0.0, 0.01, -0.03]},
        "spatial": {"x": 3.2, "y": 1.1, "z": 0.9, "roll": 0.01, "pitch": -0.02, "yaw": 1.57,
                    "linear_velocity_mps": 0.6, "angular_velocity_rps": 0.1},
        "temperatures_c": {"cpu": 63.4, "ambient": 24.5},
        "extra": {"wifi_dbm": -58.0},
    }
    assert client.post("/api/v1/telemetry", json=payload).status_code == 202

    # /sensors endpoint reflects the snapshot.
    sensors = client.get(f"/api/dashboard/robot/{rid}/sensors").json()["sensors"]
    assert sensors["battery"]["temperature_c"] == 34.2
    assert len(sensors["motors"]) == 2
    assert sensors["spatial"]["yaw"] == 1.57
    assert sensors["imu"]["accel"] == [0.1, -0.02, 9.79]
    assert sensors["temperatures_c"]["cpu"] == 63.4
    assert sensors["extra"]["wifi_dbm"] == -58.0

    # RobotDetail carries the same snapshot + battery drives the summary.
    detail = client.get(f"/api/dashboard/robot/{rid}").json()
    assert detail["sensors"]["battery"]["pct"] == 73.5
    assert detail["battery_pct"] == 73.5


def test_battery_and_spatial_drive_runtime_via_store():
    rid = _a_robot()
    store.ingest_telemetry(TelemetryIn(
        robot_id=rid, vendor="Unitree", model="G1", facility_id="f", delta_meters=0.02,
        battery=BatteryTelemetry(pct=41.0, temperature_c=30.0),
        spatial=SpatialTelemetry(x=5.0, y=6.0, yaw=0.5),
        motors=[MotorTelemetry(joint="ankle", temperature_c=44.0)],
        imu=ImuTelemetry(accel=[0, 0, 9.8], gyro=[0, 0, 0]),
    ))
    snap = store.latest_sensors(rid)
    assert snap is not None and snap.battery.pct == 41.0
    assert store.robots[rid].battery_pct == 41.0
    assert store.robots[rid].pose_internal.x == 5.0


def test_sensors_endpoint_404_for_unknown_robot():
    assert client.get("/api/dashboard/robot/nope-999/sensors").status_code == 404
