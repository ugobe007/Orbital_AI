"""The simulated facility's demo Digital Net cell: calibration and camera occupancy."""
from orbital_cloud import demo_rig
from orbital_cloud.occupancy import camera_occupancy
from orbital_cloud.rig_calibration import calibration_store


def test_demo_calibration_names_the_bumped_marker():
    calibration_store.clear()
    demo_rig.seed_calibration("fac-demo")
    report = calibration_store.get("fac-demo")
    assert report is not None and not report["ok"]
    assert any(m["status"] == "likely_error" for m in report["markers"])
    calibration_store.clear()


def test_demo_occupancy_blocks_the_pallet_and_outlives_one_renewal():
    camera_occupancy.clear()
    body = demo_rig.occupancy_payload()
    assert body.source == demo_rig.SOURCE and body.cells
    assert body.ttl_s > demo_rig.RENEW_S
    camera_occupancy.ingest("fac-demo", body)
    assert camera_occupancy.rects("fac-demo")
    camera_occupancy.clear()
