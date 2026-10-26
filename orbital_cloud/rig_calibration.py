"""Latest rig-calibration report per facility (Sprint H).

An installer (or ``scripts/calibrate_rig.py --upload``) posts the raw marker captures; the cloud
solves them with the same solver the edge uses, keeps the newest report per facility, and the
dashboard shows which markers and cameras need attention. In-memory: a calibration is cheap to
re-run, and the marker map the edge actually uses lives on the edge box.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from aria_edge.calibration import DEFAULT_RESIDUAL_M, solve_dict

from .models import CalibrationIn

_MAX_FACILITIES = 64


class CalibrationStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_facility: dict[str, dict] = {}

    def solve_and_store(self, facility_id: str, body: CalibrationIn, actor: Optional[str] = None) -> dict:
        data = body.model_dump(mode="json")
        threshold = body.threshold_m or DEFAULT_RESIDUAL_M
        report = solve_dict(data, threshold_m=threshold).to_dict()
        entry = {"facility_id": facility_id, "received_at": time.time(), "actor": actor, **report}
        with self._lock:
            self._by_facility[facility_id] = entry
            while len(self._by_facility) > _MAX_FACILITIES:
                oldest = min(self._by_facility, key=lambda k: self._by_facility[k]["received_at"])
                del self._by_facility[oldest]
        return entry

    def get(self, facility_id: str) -> Optional[dict]:
        return self._by_facility.get(facility_id)

    def clear(self) -> None:
        with self._lock:
            self._by_facility.clear()


calibration_store = CalibrationStore()
