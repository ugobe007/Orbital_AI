"""Sprint C — TelemetryStore, occupancy map, mTLS harness, dashboard RBAC."""
from __future__ import annotations

import os
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aria_edge.edge_agent import CloudSync
from aria_edge.mtls import MtlsConfig, load_mtls_config
from orbital_cloud.main import app
from orbital_cloud.occupancy import synthetic_occupancy_grid
from orbital_cloud.rbac import Role, resolve_role
from orbital_cloud.store import Store
from orbital_cloud.telemetry_store import InfluxTelemetryStore, MemoryTelemetryStore
from orbital_cloud.models import TelemetryIn


client = TestClient(app)


def test_memory_telemetry_store_feeds_benchmark():
    mem = MemoryTelemetryStore()
    store = Store(telemetry_backend=mem)
    rid = next(iter(store.robots))
    store.ingest_telemetry(TelemetryIn(
        robot_id=rid, vendor="Unitree", model="G1",
        facility_id="facility-sf-001", delta_meters=0.12,
    ))
    assert len(mem.series(rid)) == 1
    vendor = store.robots[rid].vendor
    bench = store.benchmark(vendor)
    assert bench.samples >= 1


def test_influx_store_dual_writes_to_memory_even_when_url_unreachable():
    influx = InfluxTelemetryStore(url="http://127.0.0.1:1", token="x")
    influx.append("rbt-01", 1.0, 0.05)
    assert list(influx.series("rbt-01")) == [(1.0, 0.05)]
    assert influx.write_errors >= 1


def test_synthetic_occupancy_has_data_and_occupied_cells():
    grid = synthetic_occupancy_grid("facility-sf-001")
    assert grid["encoding"] == "occupancy_grid_v1"
    assert len(grid["data"]) == grid["width"] * grid["height"]
    assert grid["stats"]["occupied"] > 0
    assert 100 in grid["data"]


def test_edge_map_endpoint_returns_occupancy():
    r = client.get("/api/v1/map/facility-sf-001")
    assert r.status_code == 200
    body = r.json()
    assert "data" in body and body["stats"]["occupied"] > 0
    assert body["width"] == int(round(24.0 / 0.05))


def test_cloud_sync_pull_map_from_cache():
    sync = CloudSync(enabled=False)
    sync.maps["facility-sf-001"] = {"width": 10, "data": [0]}
    assert sync.pull_map()["width"] == 10


def test_mtls_config_from_env(tmp_path, monkeypatch):
    cert = tmp_path / "c.crt"
    key = tmp_path / "c.key"
    cert.write_text("CERT")
    key.write_text("KEY")
    monkeypatch.setenv("ORBITAL_MTLS_CERT", str(cert))
    monkeypatch.setenv("ORBITAL_MTLS_KEY", str(key))
    monkeypatch.setenv("ORBITAL_MTLS_CA", str(cert))
    cfg = load_mtls_config()
    assert cfg is not None
    kw = cfg.httpx_kwargs()
    assert kw["cert"] == (str(cert), str(key))
    assert kw["verify"] == str(cert)


def _gen_certs(outdir: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "gen_mtls_certs.sh"
    subprocess.run(["bash", str(script), str(outdir)], check=True, capture_output=True)


@pytest.mark.skipif(
    subprocess.call(["which", "openssl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0,
    reason="openssl not available",
)
def test_mtls_roundtrip_with_client_cert(tmp_path):
    _gen_certs(tmp_path)
    ca, server_crt, server_key = tmp_path / "ca.crt", tmp_path / "server.crt", tmp_path / "server.key"
    client_crt, client_key = tmp_path / "client.crt", tmp_path / "client.key"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", 0))
            _ = self.rfile.read(length)
            self.send_response(202)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *_args):  # silence
            return

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(server_crt), str(server_key))
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(str(ca))

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        sync = CloudSync(
            base_url=f"https://127.0.0.1:{port}",
            enabled=True,
            mtls=MtlsConfig(cert=str(client_crt), key=str(client_key), ca=str(ca)),
        )
        sync.post_telemetry({
            "robot_id": "rbt-01", "vendor": "Unitree", "model": "G1",
            "facility_id": "facility-sf-001", "delta_meters": 0.01,
        })
        assert len(sync.telemetry_sent) == 1
    finally:
        httpd.shutdown()


def test_rbac_viewer_cannot_estop(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    rid = client.get("/api/dashboard/fleet", headers={"X-Orbital-Role": "viewer"}).json()["robots"][0]["id"]
    r = client.post(f"/api/dashboard/robot/{rid}/estop", headers={"X-Orbital-Role": "viewer"})
    assert r.status_code == 403


def test_rbac_operator_can_estop(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    rid = client.get("/api/dashboard/fleet", headers={"X-Orbital-Role": "operator"}).json()["robots"][0]["id"]
    r = client.post(f"/api/dashboard/robot/{rid}/estop", headers={"X-Orbital-Role": "operator"})
    assert r.status_code == 200
    client.post(f"/api/dashboard/robot/{rid}/resume", headers={"X-Orbital-Role": "operator"})


def test_rbac_viewer_can_read_fleet(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    r = client.get("/api/dashboard/fleet", headers={"X-Orbital-Role": "viewer"})
    assert r.status_code == 200


def test_rbac_enforce_anon_is_viewer_by_default(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.delenv("ORBITAL_RBAC_ANON_VIEWER", raising=False)
    r = client.get("/api/dashboard/fleet")
    assert r.status_code == 200  # public read
    rid = r.json()["robots"][0]["id"]
    assert client.post(f"/api/dashboard/robot/{rid}/estop").status_code == 403


def test_rbac_enforce_strict_requires_token(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_ANON_VIEWER", "0")
    r = client.get("/api/dashboard/fleet")
    assert r.status_code == 401


def test_resolve_role_token_map(monkeypatch):
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "admin:adm-secret,viewer:view-secret")
    assert resolve_role(authorization="Bearer adm-secret") == Role.ADMIN
    assert resolve_role(authorization="Bearer view-secret") == Role.VIEWER


def test_resolve_role_strips_wrapping_quotes(monkeypatch):
    """If someone pasted quotes into Fly, strip one wrapping layer so tokens still match."""
    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", '"admin:adm-secret,viewer:view-secret"')
    assert resolve_role(authorization="Bearer adm-secret") == Role.ADMIN
    assert resolve_role(authorization='Bearer "view-secret"') == Role.VIEWER
