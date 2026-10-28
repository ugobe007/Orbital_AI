#!/usr/bin/env python3
"""Record the live floor to disk: every robot, person and tracked object (forklifts, pallets,
trucks, carts), every alert, and the value-bar totals, on one clock.

Usage:
  python3 scripts/capture_floor.py --url https://orbital-ai.io --out captures/sf-evening --duration 600
  python3 scripts/capture_floor.py --url http://localhost:8099 --out captures/demo --hz 5
  python3 scripts/capture_floor.py --url https://orbital-ai.io --out captures/x --poll   # no WebSocket

Stops after ``--duration`` seconds, or on Ctrl-C; either way the files are finished cleanly.
By default it listens to the console's live WebSocket feed; ``--poll`` reads the REST API instead
(for networks that block WebSockets). Set ORBITAL_TOKEN to a viewer (or higher) bearer token
when the cloud enforces RBAC; without one the floor layout and, in poll mode, everything else
may be refused.

Output folder:
  meta.json       where and when, the floor layout, whether the fleet was simulated, totals
  frames.jsonl    one line per sample: t, robots, workers, objects, kpis
  alerts.jsonl    one line per alert, as raised
  kpis.csv        value-bar totals once per sample, for a spreadsheet
  summary.md      what happened: per-robot distance and time by state, objects seen, alerts,
                  and what the floor delivered during the capture

Frame fields: robots carry the camera pose (x, y, theta), the robot's own pose (ix, iy,
itheta), drift_m, state, battery, task, phase, mode and error; workers carry x, y, role and
status; objects carry label, x, y, yaw, vx, vy and activity. Distances are metres, times are
Unix seconds.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import signal
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Optional

KPI_FIELDS = ["pallets_shipped", "trucks_loaded", "deliveries", "slowdowns", "safety_stops", "fleet_busy_pct"]
# Jumps longer than this between samples are teleports (resets, restarts), not distance driven.
MAX_STEP_M = 3.0


def _r(v: Optional[float], nd: int = 3) -> Optional[float]:
    return None if v is None else round(float(v), nd)


def robot_row(r: dict) -> dict:
    ex, ins = r.get("pose_external") or {}, r.get("pose_internal") or {}
    return {
        "id": r["id"], "vendor": r.get("vendor"), "model": r.get("model"), "state": r.get("state"),
        "x": _r(ex.get("x")), "y": _r(ex.get("y")), "theta": _r(ex.get("theta")),
        "ix": _r(ins.get("x")), "iy": _r(ins.get("y")), "itheta": _r(ins.get("theta")),
        "drift_m": _r(r.get("drift_delta_m"), 4), "battery": _r(r.get("battery_pct"), 1),
        "task": r.get("current_task"), "phase": r.get("mission_phase"), "mode": r.get("control_mode"),
        "error": r.get("error_code"), "simulated": bool(r.get("simulated")),
    }


def worker_row(w: dict) -> dict:
    pose = w.get("pose") or {}
    return {"id": w["id"], "role": w.get("role"), "status": w.get("status"),
            "x": _r(pose.get("x")), "y": _r(pose.get("y"))}


def object_row(o: dict) -> dict:
    return {"id": o["id"], "label": o.get("label"), "source": o.get("source"),
            "x": _r(o.get("x")), "y": _r(o.get("y")), "yaw": _r(o.get("yaw")),
            "vx": _r(o.get("vx")), "vy": _r(o.get("vy")), "activity": o.get("activity"),
            "confidence": _r(o.get("confidence"), 2)}


class FloorRecorder:
    """Turns live messages (the same ones the console receives) into capture files."""

    def __init__(self, out: Path, *, hz: float = 2.0, url: str = "", layout: Optional[dict] = None,
                 facility: Optional[dict] = None) -> None:
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.min_gap = 1.0 / hz if hz > 0 else 0.0
        self.meta = {"url": url, "facility": facility, "hz": hz, "started_at": None, "ended_at": None,
                     "layout": layout, "recorder": "orbital-floor-capture-v1"}
        self._frames = (out / "frames.jsonl").open("w")
        self._alerts = (out / "alerts.jsonl").open("w")
        self._kpi_file = (out / "kpis.csv").open("w", newline="")
        self._kpi_csv = csv.writer(self._kpi_file)
        self._kpi_csv.writerow(["t", *KPI_FIELDS])
        self.robots: list[dict] = []
        self.workers: list[dict] = []
        self.objects: list[dict] = []
        self.kpis: Optional[dict] = None
        self.first_kpis: Optional[dict] = None
        self.frame_count = 0
        self._last_frame_t = -1e18
        self._next_due = -1e18
        self._seen_alerts: set[str] = set()
        self.alert_types: Counter = Counter()
        self.simulated = False
        # per-robot running stats
        self._last_pose: dict[str, tuple[float, float, float]] = {}
        self.distance: defaultdict[str, float] = defaultdict(float)
        self.state_s: defaultdict[str, Counter] = defaultdict(Counter)
        self.max_drift: defaultdict[str, float] = defaultdict(float)
        self.robot_info: dict[str, dict] = {}
        self.object_ids: defaultdict[str, set] = defaultdict(set)

    def on_message(self, msg: dict, now: float) -> None:
        kind = msg.get("type")
        if kind == "fleet":
            self.robots = [robot_row(r) for r in msg.get("robots", [])]
            if "workers" in msg:
                self.workers = [worker_row(w) for w in msg["workers"]]
            if msg.get("kpis"):
                self.kpis = msg["kpis"]
                self.first_kpis = self.first_kpis or msg["kpis"]
            self._maybe_frame(now)
        elif kind == "workers":
            self.workers = [worker_row(w) for w in msg.get("workers", [])]
        elif kind == "objects":
            self.objects = [object_row(o) for o in msg.get("objects", [])]
        elif kind == "alert" and msg.get("alert"):
            self.on_alert(msg["alert"], now)

    def on_alert(self, alert: dict, now: float) -> None:
        aid = alert.get("id") or f"{alert.get('robot_id')}:{alert.get('ts')}:{alert.get('type')}"
        if aid in self._seen_alerts:
            return
        self._seen_alerts.add(aid)
        self.alert_types[alert.get("type", "unknown")] += 1
        self._alerts.write(json.dumps({"t": _r(now), **alert}, ensure_ascii=False) + "\n")

    def mark_seen(self, alerts: list[dict]) -> None:
        """Alerts already raised before the capture began; never recorded as new."""
        for a in alerts:
            self._seen_alerts.add(a.get("id") or f"{a.get('robot_id')}:{a.get('ts')}:{a.get('type')}")

    def _maybe_frame(self, now: float) -> None:
        # Frames keep to a fixed grid, so 2 Hz from a 5 Hz feed averages 2 Hz rather than
        # rounding every gap up to the next update.
        if now + 0.02 < self._next_due or not self.robots:
            return
        nxt = self._next_due + self.min_gap
        self._next_due = nxt if nxt > now else now + self.min_gap
        dt = now - self._last_frame_t if self.frame_count else 0.0
        self._last_frame_t = now
        if self.meta["started_at"] is None:
            self.meta["started_at"] = now
        self.frame_count += 1
        for r in self.robots:
            self._track_robot(r, dt)
        for o in self.objects:
            self.object_ids[o["label"] or "unknown"].add(o["id"])
        self._frames.write(json.dumps({"t": _r(now), "robots": self.robots, "workers": self.workers,
                                       "objects": self.objects, "kpis": self.kpis}, ensure_ascii=False) + "\n")
        if self.kpis:
            self._kpi_csv.writerow([_r(now), *(self.kpis.get(k) for k in KPI_FIELDS)])

    def _track_robot(self, r: dict, dt: float) -> None:
        rid = r["id"]
        self.robot_info[rid] = {"vendor": r["vendor"], "model": r["model"]}
        self.simulated = self.simulated or r["simulated"]
        if r["x"] is not None and r["y"] is not None:
            prev = self._last_pose.get(rid)
            if prev is not None:
                step = math.hypot(r["x"] - prev[0], r["y"] - prev[1])
                if step <= MAX_STEP_M:
                    self.distance[rid] += step
            self._last_pose[rid] = (r["x"], r["y"], r["theta"] or 0.0)
        if dt > 0:
            self.state_s[rid][r["state"] or "unknown"] += dt
        self.max_drift[rid] = max(self.max_drift[rid], r["drift_m"] or 0.0)

    def finish(self, now: float) -> dict:
        self.meta["ended_at"] = self._last_frame_t if self.frame_count else now
        self.meta.update({
            "frames": self.frame_count, "alerts": sum(self.alert_types.values()),
            "simulated": self.simulated, "robots": sorted(self.robot_info),
            "kpis_start": self.first_kpis, "kpis_end": self.kpis,
        })
        for f in (self._frames, self._alerts, self._kpi_file):
            f.close()
        (self.out / "meta.json").write_text(json.dumps(self.meta, indent=2, ensure_ascii=False) + "\n")
        (self.out / "summary.md").write_text(self.summary_markdown())
        return self.meta

    def summary_markdown(self) -> str:
        m = self.meta
        start, end = m.get("started_at"), m.get("ended_at")
        dur = (end - start) if start is not None and end is not None else 0.0
        lines = [f"# Floor capture{': ' + m['facility']['name'] if m.get('facility') else ''}", ""]
        if self.simulated:
            lines += ["> SIMULATION: this capture includes simulated robots, not real hardware.", ""]
        when = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(start)) if start is not None else "no frames"
        lines += [f"- Source: {m.get('url') or 'n/a'}", f"- Started: {when}",
                  f"- Duration: {dur / 60:.1f} min, {self.frame_count} frames, {sum(self.alert_types.values())} alerts", ""]
        a, b = self.first_kpis, self.kpis
        if a and b and dur > 0:
            hours = dur / 3600
            lines += ["## Delivered during the capture", "", "| | During capture | Per hour |", "|---|---:|---:|"]
            for k, label in [("pallets_shipped", "Pallets shipped"), ("trucks_loaded", "Trucks loaded"),
                             ("deliveries", "Robot deliveries"), ("slowdowns", "Safety slow-downs"),
                             ("safety_stops", "Safety stops")]:
                d = (b.get(k) or 0) - (a.get(k) or 0)
                # Hourly rates from a few seconds of data mislead; give them from five minutes on.
                rate = f"{d / hours:.0f}" if dur >= 300 else "—"
                lines.append(f"| {label} | {d} | {rate} |")
            if b.get("fleet_busy_pct") is not None:
                lines.append(f"| Fleet busy (since server start) | {b['fleet_busy_pct']}% | |")
            lines.append("")
        if self.robot_info:
            states = sorted({s for c in self.state_s.values() for s in c})
            lines += ["## Robots", "", "| Robot | Vendor | Distance (m) | Max drift (m) | " + " | ".join(states) + " |",
                      "|---|---|---:|---:|" + "---:|" * len(states)]
            for rid in sorted(self.robot_info):
                info, total = self.robot_info[rid], sum(self.state_s[rid].values()) or 1.0
                share = " | ".join(f"{100 * self.state_s[rid][s] / total:.0f}%" for s in states)
                lines.append(f"| {rid} | {info['vendor']} {info['model']} | {self.distance[rid]:.1f} | "
                             f"{self.max_drift[rid]:.2f} | {share} |")
            lines.append("")
        if self.object_ids:
            lines += ["## Objects seen", ""] + [f"- {label}: {len(ids)}" for label, ids in sorted(self.object_ids.items())] + [""]
        if self.alert_types:
            lines += ["## Alerts", ""] + [f"- {t}: {n}" for t, n in self.alert_types.most_common()] + [""]
        return "\n".join(lines)


def _headers() -> dict:
    token = os.getenv("ORBITAL_TOKEN")
    return {"Authorization": f"Bearer {token}"} if token else {}


async def _fetch_json(http, path: str) -> Optional[dict]:
    try:
        resp = await http.get(path)
        return resp.json() if resp.status_code == 200 else None
    except Exception:  # noqa: BLE001 — the layout is optional; the capture goes on without it
        return None


async def capture(url: str, out: Path, *, duration: float, hz: float, poll: bool) -> dict:
    import httpx

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    if duration > 0:
        loop.call_later(duration, stop.set)

    async with httpx.AsyncClient(base_url=url.rstrip("/"), headers=_headers(), timeout=10.0) as http:
        layout = await _fetch_json(http, "/api/dashboard/map")
        fleet = await _fetch_json(http, "/api/dashboard/fleet")
        if layout is None:
            print("warning: could not read the floor layout (set ORBITAL_TOKEN if the cloud enforces RBAC)", file=sys.stderr)
        if layout:
            layout = {k: v for k, v in layout.items() if k not in ("objects", "camera_cells")}
        rec = FloorRecorder(out, hz=hz, url=url, layout=layout, facility=(fleet or {}).get("facility"))
        if fleet:
            rec.on_message({"type": "fleet", **fleet}, time.time())
        rec.mark_seen(await _fetch_json(http, "/api/dashboard/alerts?limit=200") or [])
        print(f"Recording {url} to {out} ({'poll' if poll else 'live feed'}, {hz:g} Hz). Ctrl-C to stop.", file=sys.stderr)
        try:
            if poll:
                await _poll(http, rec, hz, stop)
            else:
                await _listen(url, rec, stop)
        finally:
            meta = rec.finish(time.time())
    return meta


async def _poll(http, rec: FloorRecorder, hz: float, stop: asyncio.Event) -> None:
    gap = 1.0 / hz if hz > 0 else 1.0
    while not stop.is_set():
        objects = await _fetch_json(http, "/api/dashboard/objects")
        if objects:
            rec.on_message(objects, time.time())
        alerts = await _fetch_json(http, "/api/dashboard/alerts?limit=50")
        for a in reversed(alerts or []):
            rec.on_alert(a, time.time())
        fleet = await _fetch_json(http, "/api/dashboard/fleet")
        if fleet:
            rec.on_message({"type": "fleet", **fleet}, time.time())
        try:
            await asyncio.wait_for(stop.wait(), timeout=gap)
        except asyncio.TimeoutError:
            pass


async def _listen(url: str, rec: FloorRecorder, stop: asyncio.Event) -> None:
    import websockets

    ws_url = url.rstrip("/").replace("https://", "wss://").replace("http://", "ws://") + "/ws"
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(ws_url, additional_headers=_headers(), open_timeout=10) as ws:
                backoff = 1.0
                while not stop.is_set():
                    recv = asyncio.ensure_future(ws.recv())
                    halt = asyncio.ensure_future(stop.wait())
                    done, _ = await asyncio.wait({recv, halt}, return_when=asyncio.FIRST_COMPLETED)
                    if recv not in done:
                        recv.cancel()
                        return
                    halt.cancel()
                    rec.on_message(json.loads(recv.result()), time.time())
        except Exception as exc:  # noqa: BLE001 — drop, refuse or bad frame: reconnect until told to stop
            print(f"live feed dropped ({exc}); reconnecting in {backoff:.0f}s", file=sys.stderr)
            try:
                await asyncio.wait_for(stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, 30.0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="Orbital cloud base URL")
    ap.add_argument("--out", type=Path, required=True, help="folder to write the capture into")
    ap.add_argument("--duration", type=float, default=0, help="seconds to record (default: until Ctrl-C)")
    ap.add_argument("--hz", type=float, default=2.0, help="frames per second to keep (the feed runs at 5)")
    ap.add_argument("--poll", action="store_true", help="read the REST API instead of the live feed")
    args = ap.parse_args(argv)
    meta = asyncio.run(capture(args.url, args.out, duration=args.duration, hz=args.hz, poll=args.poll))
    if not meta["frames"]:
        print("no frames captured (is the URL right, and is the fleet live?)", file=sys.stderr)
        return 1
    print(f"Captured {meta['frames']} frames and {meta['alerts']} alerts in "
          f"{(meta['ended_at'] - meta['started_at']) / 60:.1f} min to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
