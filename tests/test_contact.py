"""Contact form: the homepage pilot form posts here."""
from fastapi.testclient import TestClient

from orbital_cloud import contact
from orbital_cloud.main import app

client = TestClient(app)


def _capture(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(contact.persistence, "save_inbox_message",
                        lambda **kw: saved.append(kw) or True)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    contact._hits.clear()
    return saved


def test_pilot_form_saves_website_with_message(monkeypatch):
    saved = _capture(monkeypatch)
    r = client.post("/api/contact", json={
        "name": "Ada", "email": "ada@fleet.example", "topic": "early-access",
        "website": "fleet.example", "message": "6 AMRs, 2 sites", "company_url": "",
    })
    assert r.status_code == 200 and r.json()["ok"]
    assert saved[0]["subject"] == "14-day pilot request — Ada"
    assert "6 AMRs, 2 sites" in saved[0]["body"]
    assert "Company website: fleet.example" in saved[0]["body"]


def test_honeypot_is_dropped_silently(monkeypatch):
    saved = _capture(monkeypatch)
    r = client.post("/api/contact", json={
        "name": "Bot", "email": "bot@spam.example", "company_url": "http://spam.example",
    })
    assert r.status_code == 200 and r.json()["ok"]
    assert saved == []
