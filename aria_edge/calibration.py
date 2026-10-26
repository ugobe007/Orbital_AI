"""Sprint H — camera-rig calibration with residuals.

Input is a set of *captures*. Each capture is one view (a fixed overhead camera, a Digital Net
camera, or a handheld pass) that saw two or more floor markers, with each marker's pose in that
view's own frame. The solver finds every marker's global ``(x, y, θ)``, anchored on an origin
marker, plus every capture's pose — then reports which marker or camera disagrees with the rest
and by how much, so an installer knows exactly what to fix.

The math is a 2-D pose graph solved by Gauss-Newton with Cauchy robust weights, optionally pinned
by tape-measured anchor markers, with one-at-a-time rejection of the worst observation until every
remaining residual is under the threshold.
Standard library only, so it runs in CI and on a bare edge box.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

DEFAULT_RESIDUAL_M = float(os.getenv("ORBITAL_CALIB_RESIDUAL_M", "0.05") or "0.05")
_MAX_ITER = 30
_MAX_REJECTIONS = 25
_CONVERGED_M = 1e-7
_THETA_WEIGHT = 0.2   # metres of position residual equivalent to one radian of yaw residual
_ANCHOR_WEIGHT = 10.0  # a tape-measured anchor is trusted ~10x more than one camera observation


@dataclass(frozen=True)
class Pose:
    x: float
    y: float
    theta: float = 0.0

    def apply(self, x: float, y: float) -> tuple[float, float]:
        c, s = math.cos(self.theta), math.sin(self.theta)
        return self.x + c * x - s * y, self.y + s * x + c * y

    def compose(self, local: "Pose") -> "Pose":
        x, y = self.apply(local.x, local.y)
        return Pose(x, y, _wrap(self.theta + local.theta))


@dataclass(frozen=True)
class Observation:
    marker_id: int
    x: float
    y: float
    theta: float = 0.0


@dataclass
class Capture:
    id: str
    observations: list[Observation]
    expected: Optional[Pose] = None   # configured extrinsics, when this is a fixed camera


@dataclass
class MarkerResult:
    marker_id: int
    pose: Optional[Pose]
    views: int
    rms_m: float = 0.0
    max_m: float = 0.0
    status: str = "ok"                # ok | likely_error | single_view | unanchored
    rejected_in: list[str] = field(default_factory=list)


@dataclass
class CaptureResult:
    id: str
    pose: Optional[Pose]
    markers: int
    rms_m: float = 0.0
    offset_m: Optional[float] = None
    offset_deg: Optional[float] = None
    status: str = "ok"                # ok | high_residual | moved | unanchored


@dataclass
class CalibrationReport:
    origin_marker: int
    threshold_m: float
    markers: list[MarkerResult]
    captures: list[CaptureResult]
    rms_m: float
    iterations: int
    hints: list[str]

    @property
    def ok(self) -> bool:
        return (all(m.status in ("ok", "single_view") for m in self.markers)
                and all(c.status == "ok" for c in self.captures))

    def marker_map(self) -> dict[int, Pose]:
        return {m.marker_id: m.pose for m in self.markers if m.pose is not None and m.status != "unanchored"}

    def to_dict(self) -> dict:
        def pose(p: Optional[Pose]) -> Optional[dict]:
            return None if p is None else {"x": round(p.x, 4), "y": round(p.y, 4), "theta": round(p.theta, 5)}
        return {
            "ok": self.ok,
            "origin_marker": self.origin_marker,
            "threshold_m": self.threshold_m,
            "rms_m": round(self.rms_m, 4),
            "iterations": self.iterations,
            "hints": self.hints,
            "markers": [
                {"marker_id": m.marker_id, "pose": pose(m.pose), "views": m.views,
                 "rms_m": round(m.rms_m, 4), "max_m": round(m.max_m, 4), "status": m.status,
                 "rejected_in": m.rejected_in}
                for m in self.markers
            ],
            "captures": [
                {"id": c.id, "pose": pose(c.pose), "markers": c.markers, "rms_m": round(c.rms_m, 4),
                 "offset_m": None if c.offset_m is None else round(c.offset_m, 4),
                 "offset_deg": None if c.offset_deg is None else round(c.offset_deg, 2),
                 "status": c.status}
                for c in self.captures
            ],
        }


def solve(captures: list[Capture], origin_marker: int, *, origin_pose: Pose = Pose(0.0, 0.0, 0.0),
          anchors: Optional[dict[int, tuple[float, float]]] = None,
          threshold_m: float = DEFAULT_RESIDUAL_M) -> CalibrationReport:
    """Solve the marker map. ``anchors`` are tape-measured floor positions of other markers;
    one anchor a few metres from the origin pins the map's rotation far better than the origin
    marker's own yaw can."""
    anchors = {k: v for k, v in (anchors or {}).items() if k != origin_marker}
    by_id = {c.id: c for c in captures}
    anchored = _anchored_captures(captures, origin_marker)
    inliers: dict[str, set[int]] = {
        c.id: {o.marker_id for o in c.observations} for c in captures if c.id in anchored
    }
    rejected: list[tuple[str, int]] = []

    markers, poses = _initialise(by_id, anchored, origin_marker, origin_pose)
    iterations = 0
    while True:
        markers, poses, n = _refine(by_id, inliers, markers, poses, origin_marker, origin_pose,
                                    anchors, threshold_m)
        iterations += n
        worst = _worst_observation(by_id, inliers, markers, poses)
        if worst is None or worst[2] <= threshold_m:
            break
        cid, mid, _ = worst
        if len(inliers[cid]) <= 2 or len(rejected) >= _MAX_REJECTIONS:
            break
        before = _connected(inliers, origin_marker)
        inliers[cid].discard(mid)
        if _connected(inliers, origin_marker) != before:
            inliers[cid].add(mid)
            break   # dropping it would disconnect the graph; report it instead
        rejected.append((cid, mid))

    return _report(captures, by_id, anchored, inliers, rejected, markers, poses,
                   origin_marker, threshold_m, iterations)


# ── Solver internals ───────────────────────────────────────────────────────────

def _anchored_captures(captures: list[Capture], origin_marker: int) -> set[str]:
    """Captures connected to the origin marker through shared markers."""
    marker_caps: dict[int, list[Capture]] = defaultdict(list)
    for c in captures:
        for o in c.observations:
            marker_caps[o.marker_id].append(c)
    seen_markers = {origin_marker}
    seen_caps: set[str] = set()
    frontier = [origin_marker]
    while frontier:
        mid = frontier.pop()
        for c in marker_caps.get(mid, []):
            if c.id in seen_caps:
                continue
            seen_caps.add(c.id)
            for o in c.observations:
                if o.marker_id not in seen_markers:
                    seen_markers.add(o.marker_id)
                    frontier.append(o.marker_id)
    return seen_caps


def _connected(inliers: dict[str, set[int]], origin_marker: int) -> tuple[set[str], set[int]]:
    """Captures and markers reachable from the origin marker through inlier observations."""
    seen_markers = {origin_marker}
    seen_caps: set[str] = set()
    frontier = [origin_marker]
    while frontier:
        mid = frontier.pop()
        for cid, ids in inliers.items():
            if cid in seen_caps or mid not in ids:
                continue
            seen_caps.add(cid)
            for m in ids - seen_markers:
                seen_markers.add(m)
                frontier.append(m)
    return seen_caps, seen_markers


def _initialise(by_id: dict[str, Capture], anchored: set[str], origin_marker: int,
                origin_pose: Pose) -> tuple[dict[int, Pose], dict[str, Pose]]:
    """Chain outward from the origin: place a capture from any marker it shares with the map,
    then place that capture's other markers."""
    markers: dict[int, Pose] = {origin_marker: origin_pose}
    poses: dict[str, Pose] = {}
    pending = sorted(anchored)
    while pending:
        progressed = False
        for cid in list(pending):
            known = [o for o in by_id[cid].observations if o.marker_id in markers]
            if not known:
                continue
            poses[cid] = _fit_capture(known, markers)
            for o in by_id[cid].observations:
                if o.marker_id not in markers:
                    markers[o.marker_id] = poses[cid].compose(Pose(o.x, o.y, o.theta))
            pending.remove(cid)
            progressed = True
        if not progressed:
            break
    return markers, poses


def _refine(by_id: dict[str, Capture], inliers: dict[str, set[int]], markers: dict[int, Pose],
            poses: dict[str, Pose], origin_marker: int, origin_pose: Pose,
            anchors: dict[int, tuple[float, float]],
            threshold_m: float) -> tuple[dict[int, Pose], dict[str, Pose], int]:
    """Gauss-Newton over every capture pose and every marker pose except the origin, which
    fixes the gauge. Each observation contributes a 2-D position residual and a down-weighted
    orientation residual (marker yaw is the noisiest thing a camera measures). Cauchy weights
    keep one bad observation from dragging a whole capture, so it stands out in the residuals."""
    cauchy_c = threshold_m / 2.0
    obs = [(cid, o) for cid, ids in inliers.items() if cid in poses
           for o in by_id[cid].observations if o.marker_id in ids and o.marker_id in markers]
    cap_ids = sorted({cid for cid, _ in obs})
    mark_ids = sorted({o.marker_id for _, o in obs if o.marker_id != origin_marker})
    index = {("c", c): 3 * i for i, c in enumerate(cap_ids)}
    index.update({("m", m): 3 * (len(cap_ids) + i) for i, m in enumerate(mark_ids)})
    n = 3 * (len(cap_ids) + len(mark_ids))
    markers = {**markers, origin_marker: origin_pose}

    for it in range(1, _MAX_ITER + 1):
        H = [[0.0] * n for _ in range(n)]
        b = [0.0] * n
        for cid, o in obs:
            cp, mp = poses[cid], markers[o.marker_id]
            c, s = math.cos(cp.theta), math.sin(cp.theta)
            rx = cp.x + c * o.x - s * o.y - mp.x
            ry = cp.y + s * o.x + c * o.y - mp.y
            ra = _THETA_WEIGHT * _wrap(cp.theta + o.theta - mp.theta)
            sw = 1.0 / math.sqrt(1.0 + (math.hypot(rx, ry) / cauchy_c) ** 2) if it > 1 and cauchy_c > 0 else 1.0
            ci = index[("c", cid)]
            mi = index.get(("m", o.marker_id))
            # Rows of the Jacobian for (rx, ry, ra) as sparse {column: value}.
            rows = [
                ({ci: 1.0, ci + 2: -s * o.x - c * o.y, **({mi: -1.0} if mi is not None else {})}, rx),
                ({ci + 1: 1.0, ci + 2: c * o.x - s * o.y, **({mi + 1: -1.0} if mi is not None else {})}, ry),
                ({ci + 2: _THETA_WEIGHT, **({mi + 2: -_THETA_WEIGHT} if mi is not None else {})}, ra),
            ]
            for jac, r in rows:
                for col_a, va in jac.items():
                    b[col_a] += sw * va * sw * r
                    for col_b, vb in jac.items():
                        H[col_a][col_b] += sw * va * sw * vb
        for mid, (ax, ay) in anchors.items():
            mi = index.get(("m", mid))
            if mi is None:
                continue
            mp = markers[mid]
            for k, r in ((mi, mp.x - ax), (mi + 1, mp.y - ay)):
                b[k] += _ANCHOR_WEIGHT ** 2 * r
                H[k][k] += _ANCHOR_WEIGHT ** 2
        for k in range(n):
            H[k][k] += 1e-9
        step = _solve_linear(H, [-v for v in b])
        for i, cid in enumerate(cap_ids):
            p = poses[cid]
            poses[cid] = Pose(p.x + step[3 * i], p.y + step[3 * i + 1], _wrap(p.theta + step[3 * i + 2]))
        base = 3 * len(cap_ids)
        for i, mid in enumerate(mark_ids):
            p = markers[mid]
            markers[mid] = Pose(p.x + step[base + 3 * i], p.y + step[base + 3 * i + 1],
                                _wrap(p.theta + step[base + 3 * i + 2]))
        if it > 2 and max((abs(v) for v in step), default=0.0) < _CONVERGED_M:
            return markers, poses, it
    return markers, poses, _MAX_ITER


def _solve_linear(A: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (the systems here are a few hundred wide)."""
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[pivot][col]) < 1e-15:
            continue
        M[col], M[pivot] = M[pivot], M[col]
        for r in range(col + 1, n):
            f = M[r][col] / M[col][col]
            if f:
                for k in range(col, n + 1):
                    M[r][k] -= f * M[col][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        if abs(M[r][r]) < 1e-15:
            continue
        x[r] = (M[r][n] - sum(M[r][k] * x[k] for k in range(r + 1, n))) / M[r][r]
    return x


def _fit_capture(obs: list[Observation], markers: dict[int, Pose]) -> Pose:
    """Rigid 2-D fit of a capture's local marker positions onto the current global map
    (closed-form Procrustes). With a single shared marker, its orientation fixes the rotation."""
    pairs = [((o.x, o.y), (markers[o.marker_id].x, markers[o.marker_id].y)) for o in obs]
    if len(pairs) == 1:
        o = obs[0]
        g = markers[o.marker_id]
        theta = _wrap(g.theta - o.theta)
        c, s = math.cos(theta), math.sin(theta)
        return Pose(g.x - (c * o.x - s * o.y), g.y - (s * o.x + c * o.y), theta)
    n = len(pairs)
    lx = sum(p[0][0] for p in pairs) / n
    ly = sum(p[0][1] for p in pairs) / n
    gx = sum(p[1][0] for p in pairs) / n
    gy = sum(p[1][1] for p in pairs) / n
    dot = cross = 0.0
    for (ax, ay), (bx, by) in pairs:
        ax, ay, bx, by = ax - lx, ay - ly, bx - gx, by - gy
        dot += ax * bx + ay * by
        cross += ax * by - ay * bx
    theta = math.atan2(cross, dot)
    c, s = math.cos(theta), math.sin(theta)
    return Pose(gx - (c * lx - s * ly), gy - (s * lx + c * ly), theta)


def _residual(capture_pose: Pose, o: Observation, marker: Pose) -> float:
    x, y = capture_pose.apply(o.x, o.y)
    return math.hypot(x - marker.x, y - marker.y)


def _worst_observation(by_id: dict[str, Capture], inliers: dict[str, set[int]],
                       markers: dict[int, Pose], poses: dict[str, Pose]) -> Optional[tuple[str, int, float]]:
    worst: Optional[tuple[str, int, float]] = None
    for cid, ids in inliers.items():
        if cid not in poses:
            continue
        for o in by_id[cid].observations:
            if o.marker_id in ids:
                r = _residual(poses[cid], o, markers[o.marker_id])
                if worst is None or r > worst[2]:
                    worst = (cid, o.marker_id, r)
    return worst


def _report(captures: list[Capture], by_id: dict[str, Capture], anchored: set[str],
            inliers: dict[str, set[int]], rejected: list[tuple[str, int]],
            markers: dict[int, Pose], poses: dict[str, Pose], origin_marker: int,
            threshold_m: float, iterations: int) -> CalibrationReport:
    per_marker: dict[int, list[float]] = defaultdict(list)
    per_capture: dict[str, list[float]] = defaultdict(list)
    views: dict[int, int] = defaultdict(int)
    for c in captures:
        for o in c.observations:
            views[o.marker_id] += 1
    for cid, ids in inliers.items():
        for o in by_id[cid].observations:
            if o.marker_id in ids and cid in poses:
                r = _residual(poses[cid], o, markers[o.marker_id])
                per_marker[o.marker_id].append(r)
                per_capture[cid].append(r)
    all_r = [r for rs in per_marker.values() for r in rs]
    rejected_in: dict[int, list[str]] = defaultdict(list)
    worst_rejected: dict[int, float] = defaultdict(float)
    for cid, mid in rejected:
        rejected_in[mid].append(cid)
        for o in by_id[cid].observations:
            if o.marker_id == mid and cid in poses and mid in markers:
                worst_rejected[mid] = max(worst_rejected[mid], _residual(poses[cid], o, markers[mid]))

    hints: list[str] = []
    marker_ids = sorted({o.marker_id for c in captures for o in c.observations})
    m_out: list[MarkerResult] = []
    for mid in marker_ids:
        rs = per_marker.get(mid, [])
        res = MarkerResult(mid, markers.get(mid), views.get(mid, 0), _rms(rs),
                           max(max(rs, default=0.0), worst_rejected.get(mid, 0.0)),
                           rejected_in=sorted(rejected_in.get(mid, [])))
        if mid not in markers:
            res.status = "unanchored"
        elif res.rejected_in or res.max_m > threshold_m:
            res.status = "likely_error"
        elif res.views < 2 and mid != origin_marker:
            res.status = "single_view"
        m_out.append(res)

    c_out: list[CaptureResult] = []
    for c in captures:
        res = CaptureResult(c.id, poses.get(c.id), len(c.observations), _rms(per_capture.get(c.id, [])))
        if c.id not in anchored or c.id not in poses:
            res.status = "unanchored"
        else:
            if c.expected is not None:
                p = poses[c.id]
                res.offset_m = math.hypot(p.x - c.expected.x, p.y - c.expected.y)
                res.offset_deg = abs(math.degrees(_wrap(p.theta - c.expected.theta)))
            if res.offset_m is not None and (res.offset_m > threshold_m or res.offset_deg > 1.0):
                res.status = "moved"
            elif res.rms_m > threshold_m:
                res.status = "high_residual"
        c_out.append(res)

    for m in m_out:
        if m.status != "likely_error" or m.pose is None:
            continue
        spread = _spread(m.marker_id, by_id, poses)
        where = f" between captures {', '.join(spread[1])}" if spread[1] else ""
        hints.append(
            f"Marker {m.marker_id} near ({m.pose.x:.1f}, {m.pose.y:.1f}) disagrees by "
            f"{spread[0] * 100:.0f} cm{where}. It was probably bumped or misdetected: re-seat it, "
            f"then re-capture this area."
        )
    for c in c_out:
        if c.status == "moved":
            hints.append(
                f"Camera {c.id} is {c.offset_m * 100:.0f} cm and {c.offset_deg:.1f}° away from its "
                f"configured pose. It has moved: re-mount it or update its extrinsics."
            )
        elif c.status == "high_residual":
            hints.append(
                f"Capture {c.id} fits the marker map poorly ({c.rms_m * 100:.1f} cm RMS). Check the "
                f"lens focus and that the markers in view are flat, then re-capture."
            )
    unanchored = [m.marker_id for m in m_out if m.status == "unanchored"]
    if unanchored:
        hints.append(
            f"{_markers_phrase(unanchored)} not linked to origin marker {origin_marker}. "
            f"Add a capture that sees one of them together with a marker from the main group."
        )
    single = [m.marker_id for m in m_out if m.status == "single_view"]
    if single:
        hints.append(
            f"{_markers_phrase(single, 'was', 'were')} seen in only one capture, so "
            f"{'it' if len(single) == 1 else 'they'} can't be cross-checked. Add an overlapping capture."
        )

    return CalibrationReport(origin_marker, threshold_m, m_out, c_out, _rms(all_r), iterations, hints)


def _spread(marker_id: int, by_id: dict[str, Capture], poses: dict[str, Pose]) -> tuple[float, list[str]]:
    """Largest disagreement between any two captures' placement of one marker."""
    placed = [(cid, poses[cid].apply(o.x, o.y)) for cid, c in by_id.items() if cid in poses
              for o in c.observations if o.marker_id == marker_id]
    best = (0.0, [])
    for i, (ca, a) in enumerate(placed):
        for cb, b in placed[i + 1:]:
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d > best[0]:
                best = (d, [ca, cb])
    return best


def _markers_phrase(ids: list[int], one: str = "is", many: str = "are") -> str:
    if len(ids) == 1:
        return f"Marker {ids[0]} {one}"
    return f"Markers {', '.join(map(str, ids))} {many}"


def _rms(values: list[float]) -> float:
    return math.sqrt(sum(v * v for v in values) / len(values)) if values else 0.0


def _wrap(a: float) -> float:
    return math.atan2(math.sin(a), math.cos(a))


# ── I/O ────────────────────────────────────────────────────────────────────────

def solve_dict(data: dict, threshold_m: float = DEFAULT_RESIDUAL_M) -> CalibrationReport:
    caps, origin, origin_pose = captures_from_dict(data)
    return solve(caps, origin, origin_pose=origin_pose, anchors=anchors_from_dict(data),
                 threshold_m=threshold_m)


def anchors_from_dict(data: dict) -> dict[int, tuple[float, float]]:
    """``"anchors": [{"marker_id", "x", "y"}]`` — tape-measured marker positions."""
    return {int(a["marker_id"]): (float(a["x"]), float(a["y"])) for a in data.get("anchors") or []}


def captures_from_dict(data: dict) -> tuple[list[Capture], int, Pose]:
    """Parse ``{"origin_marker", "origin_pose"?, "anchors"?, "captures": [{"id",
    "expected_pose"?, "observations": [{"marker_id", "x", "y", "theta"?}]}]}``."""
    caps = []
    for c in data.get("captures", []):
        exp = c.get("expected_pose")
        caps.append(Capture(
            id=str(c["id"]),
            observations=[Observation(int(o["marker_id"]), float(o["x"]), float(o["y"]),
                                      float(o.get("theta", 0.0))) for o in c.get("observations", [])],
            expected=Pose(float(exp["x"]), float(exp["y"]), float(exp.get("theta", 0.0))) if exp else None,
        ))
    op = data.get("origin_pose") or {}
    return caps, int(data.get("origin_marker", 0)), Pose(float(op.get("x", 0.0)), float(op.get("y", 0.0)),
                                                         float(op.get("theta", 0.0)))


def captures_from_frames(paths: Iterable[str | Path]) -> list[Capture]:
    """Recorded ArUco frames (``testdata/aruco`` format): each frame is one capture, using the
    marker's in-view translation and yaw. Files without a ``markers`` list are skipped; a camera
    that recorded several frames gets one capture per frame (``cam-0:frame_002``)."""
    caps = []
    seen: set[str] = set()
    for path in sorted(Path(p) for p in paths):
        data = json.loads(path.read_text())
        if not isinstance(data.get("markers"), list) or not data["markers"]:
            continue
        cid = str(data.get("capture_id") or data.get("camera_id") or path.stem)
        if cid in seen:
            cid = f"{cid}:{path.stem}"
        seen.add(cid)
        cal = data.get("calibration")
        caps.append(Capture(
            id=cid,
            observations=[Observation(int(m["marker_id"]), float(m["tvec"][0]), float(m["tvec"][1]),
                                      float(m.get("yaw", 0.0))) for m in data.get("markers", [])],
            expected=Pose(float(cal.get("x", 0.0)), float(cal.get("y", 0.0)), float(cal.get("yaw", 0.0)))
            if cal else None,
        ))
    return caps
