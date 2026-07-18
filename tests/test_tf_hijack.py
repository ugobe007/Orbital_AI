"""Golden tests for Module 2 TF Hijack math + inject path (Sprint A1)."""
import math

from aria_edge.types import Pose2D
from aria_edge.waypoint_generator import (
    WaypointInjector,
    apply_transform,
    calculate_drift_delta,
    get_lookahead_waypoint,
)


def _matmul(t, xy):
    return apply_transform(xy, t)


def test_t_delta_pure_translation_maps_external_to_internal():
    ext = Pose2D(1.0, 2.0, 0.0)
    internal = Pose2D(1.3, 2.1, 0.0)
    t = calculate_drift_delta(ext, internal)
    x, y = _matmul(t, (ext.x, ext.y))
    assert math.isclose(x, internal.x, abs_tol=1e-9)
    assert math.isclose(y, internal.y, abs_tol=1e-9)


def test_t_delta_with_yaw_matches_guide_matrix_shape():
    ext = Pose2D(0.0, 0.0, 0.0)
    internal = Pose2D(0.5, 0.0, math.pi / 2)
    t = calculate_drift_delta(ext, internal)
    # Guide: [[c,-s,dx],[s,c,dy],[0,0,1]] with dθ = π/2 → c=0,s=1, dx=0.5, dy=0
    assert math.isclose(t[0][0], 0.0, abs_tol=1e-9)
    assert math.isclose(t[0][1], -1.0, abs_tol=1e-9)
    assert math.isclose(t[0][2], 0.5, abs_tol=1e-9)
    assert math.isclose(t[1][0], 1.0, abs_tol=1e-9)
    assert math.isclose(t[1][1], 0.0, abs_tol=1e-9)


def test_apply_transform_shifts_absolute_waypoint_by_translation_delta():
    t = calculate_drift_delta(Pose2D(0.0, 0.0), Pose2D(0.2, -0.1))
    w_int = apply_transform((1.0, 1.0), t)
    assert math.isclose(w_int[0], 1.2, abs_tol=1e-9)
    assert math.isclose(w_int[1], 0.9, abs_tol=1e-9)


def test_lookahead_skips_near_points_then_returns_far_enough():
    traj = [(0.05, 0.0), (0.10, 0.0), (0.30, 0.0), (1.0, 0.0)]
    hit = get_lookahead_waypoint((0.0, 0.0), traj, lookahead_distance=0.20)
    assert hit is not None
    idx, pt = hit
    assert idx == 2 and pt == (0.30, 0.0)


def test_lookahead_exhausted_returns_none():
    traj = [(0.05, 0.0), (0.10, 0.0)]
    assert get_lookahead_waypoint((0.0, 0.0), traj, lookahead_distance=0.20) is None


def test_injector_tick_produces_w_internal_and_advances_index():
    inj = WaypointInjector(lookahead_m=0.20)
    inj.set_trajectory("rbt-01", [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0)])
    # External at origin; internal drifted +0.1 m in x → W_internal = W_abs + 0.1
    tick = inj.tick("rbt-01", Pose2D(0.0, 0.0), Pose2D(0.1, 0.0))
    assert not tick.trajectory_complete
    assert tick.w_abs == (0.5, 0.0)
    assert tick.w_internal is not None
    assert math.isclose(tick.w_internal[0], 0.6, abs_tol=1e-9)
    assert math.isclose(tick.w_internal[1], 0.0, abs_tol=1e-9)
