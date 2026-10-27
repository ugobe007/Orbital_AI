"""Demo Digital Net cell for the simulated facility.

With the simulator on, nothing uploads a rig calibration or camera occupancy, so the
dashboard's Rig Calibration panel and camera-blocked cells would sit empty. This replays the
synthetic rig fixtures through the same code paths a real edge upload takes: one calibration
with a bumped marker (so the panel shows a marker to re-seat), and a pallet the eight-camera
ring sees in the corridor between racks C and G, renewed before its slice lapses.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aria_edge.multiview import OccupancyVolume, load_rig, triangulate_tracks

from .config import settings
from .models import CalibrationIn, CameraOccupancyIn
from .occupancy import camera_occupancy
from .rig_calibration import calibration_store

log = logging.getLogger(__name__)

_TESTDATA = Path(__file__).resolve().parent.parent / "testdata"
CALIBRATION_FIXTURE = _TESTDATA / "calibration" / "rig_bumped_marker.json"
RIG_FIXTURE = _TESTDATA / "multiview" / "rig_8cam.json"
FRAMES_FIXTURE = _TESTDATA / "multiview" / "pallet_frames.json"
SOURCE = "digital-net-demo"
RENEW_S = 60.0
TTL_S = 180.0


def seed_calibration(facility_id: str) -> None:
    data = json.loads(CALIBRATION_FIXTURE.read_text())
    data.pop("truth", None)
    calibration_store.solve_and_store(facility_id, CalibrationIn(**data), actor="demo")


def occupancy_payload() -> CameraOccupancyIn:
    rig = load_rig(RIG_FIXTURE)
    frames = json.loads(FRAMES_FIXTURE.read_text())["frames"]
    volume = OccupancyVolume()
    for frame in frames:
        tracks = {tid: [(str(c), float(u), float(v)) for c, u, v in s] for tid, s in frame["tracks"].items()}
        report = triangulate_tracks(rig, tracks)
        volume.add_points([tri.point for _, tri in report.points], now=float(frame["t"]))
    return CameraOccupancyIn(**volume.payload(float(frames[-1]["t"]), ttl_s=TTL_S, source=SOURCE))


async def run() -> None:
    facility_id = settings.facility_id
    try:
        if calibration_store.get(facility_id) is None:
            seed_calibration(facility_id)
        occupancy = occupancy_payload()
    except Exception:
        log.exception("demo rig fixtures unavailable; skipping")
        return
    while True:
        camera_occupancy.ingest(facility_id, occupancy)
        await asyncio.sleep(RENEW_S)
