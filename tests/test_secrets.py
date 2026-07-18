"""OEM env secret loader + prod security flag snapshot."""
from fleet_adapters.secrets import load_oem_credentials, resolve_oem_credentials
from orbital_cloud.secrets import prod_security_flags


def test_load_arc_api_key(monkeypatch):
    monkeypatch.setenv("ORBITAL_SECRET_ARC_API_KEY", "k-arc")
    assert load_oem_credentials("Agility Robotics") == {"api_key": "k-arc"}


def test_load_spot_json(monkeypatch):
    monkeypatch.setenv(
        "ORBITAL_SECRET_SPOT_JSON",
        '{"username":"u","password":"p"}',
    )
    creds = load_oem_credentials("Boston Dynamics")
    assert creds["username"] == "u" and creds["password"] == "p"


def test_explicit_credentials_win(monkeypatch):
    monkeypatch.setenv("ORBITAL_SECRET_ARC_API_KEY", "from-env")
    got = resolve_oem_credentials("Agility Robotics", {"api_key": "from-arg"})
    assert got == {"api_key": "from-arg"}


def test_prod_security_flags(monkeypatch):
    monkeypatch.delenv("ORBITAL_RBAC_ENFORCE", raising=False)
    monkeypatch.delenv("ORBITAL_STRICT_OEM_SCOPES", raising=False)
    monkeypatch.delenv("ORBITAL_RBAC_TOKENS", raising=False)
    flags = prod_security_flags()
    assert flags["rbac_enforce"] is False
    assert flags["strict_oem_scopes"] is False
    assert flags["rbac_tokens_configured"] is False

    monkeypatch.setenv("ORBITAL_RBAC_ENFORCE", "1")
    monkeypatch.setenv("ORBITAL_STRICT_OEM_SCOPES", "1")
    monkeypatch.setenv("ORBITAL_RBAC_TOKENS", "admin:x")
    flags = prod_security_flags()
    assert flags["rbac_enforce"] and flags["strict_oem_scopes"] and flags["rbac_tokens_configured"]
