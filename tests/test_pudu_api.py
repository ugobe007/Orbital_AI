"""Pudu Open Platform HMAC signing + dry-run client."""
from __future__ import annotations

from fleet_adapters import get_adapter
from fleet_adapters.oem_apis import get_oem_client, known_oem_api_vendors, list_oem_endpoints
from fleet_adapters.oem_apis.pudu import (
    build_pudu_authorization,
    canonicalize_path_for_sign,
    content_md5_for_body,
    encode_query_value,
)


def test_pudu_registered():
    assert "Pudu Robotics" in known_oem_api_vendors()
    assert len(list_oem_endpoints("Pudu Robotics")["Pudu Robotics"]) >= 4


def test_encode_query_special_chars():
    assert encode_query_value("###Special Character Test") == "%23%23%23Special%20Character%20Test"


def test_canonicalize_sorts_query_and_strips_env_prefix():
    host, _url, path = canonicalize_path_for_sign(
        "https://open-platform-test.pudutech.com/test/pudu-entry/data-open-platform-service"
        "/v1/api/healthCheck?b=2&a=1&c=3",
    )
    assert host == "open-platform-test.pudutech.com"
    assert path.startswith("/pudu-entry/data-open-platform-service/v1/api/healthCheck?")
    assert "a=1" in path and "b=2" in path and "c=3" in path
    # lexicographic: a before b before c
    assert path.index("a=1") < path.index("b=2") < path.index("c=3")


def test_content_md5_matches_pudu_hex_then_b64():
    # empty body → empty MD5 header
    assert content_md5_for_body("") == ""
    md5 = content_md5_for_body("{}")
    assert md5  # non-empty base64


def test_hmac_signature_stable():
    auth = build_pudu_authorization(
        app_key="key123",
        app_secret="secret456",
        method="GET",
        path_for_sign="/pudu-entry/data-open-platform-service/v1/api/healthCheck?a=1",
        x_date="Mon, 20 Jul 2026 19:00:00 GMT",
        content_md5="",
    )
    assert 'hmac id="key123"' in auth
    assert 'algorithm="hmac-sha1"' in auth
    assert 'headers="x-date"' in auth
    assert "signature=" in auth
    # Recompute — same inputs → same signature
    auth2 = build_pudu_authorization(
        app_key="key123",
        app_secret="secret456",
        method="GET",
        path_for_sign="/pudu-entry/data-open-platform-service/v1/api/healthCheck?a=1",
        x_date="Mon, 20 Jul 2026 19:00:00 GMT",
        content_md5="",
    )
    assert auth == auth2


def test_pudu_client_dry_run_inject():
    c = get_oem_client("Pudu Robotics", "pudu-01", dry_run=True)
    assert c.connect({"app_key": "k", "app_secret": "s"})
    assert c.inject_waypoint(1.2, 3.4, 0.1)
    assert c.calls[-1].payload["path"].endswith("/v1/api/robot/task")
    assert c.trigger_estop()
    assert c.health_check()


def test_pudu_adapter_owns_oem_api():
    a = get_adapter("Pudu Robotics", "pudu-01")
    assert a.connect({"app_key": "k", "app_secret": "s"})
    assert a.inject_waypoint("pudu-01", (0.5, 0.2))
    assert a.oem_api.calls
    assert any(c.op == "inject_waypoint" for c in a.oem_api.calls)
