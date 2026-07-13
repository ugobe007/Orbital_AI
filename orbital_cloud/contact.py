"""Contact form endpoint — turns the marketing site's CTAs into real inbound leads.

The landing page posts here instead of using dead ``mailto:`` links. We relay the
submission to the team inbox via Resend and fire a courtesy auto-reply to the sender.

Configuration (env):
  RESEND_API_KEY        — Resend sending key (required for delivery; without it we 503).
  ORBITAL_CONTACT_TO    — where submissions land (default: hello@orbital-ai.io).
  ORBITAL_CONTACT_FROM  — verified sender (default: "Orbital AI <hello@orbital-ai.io>").
  ORBITAL_CONTACT_REPLY — auto-reply from address (defaults to ORBITAL_CONTACT_FROM).
"""
from __future__ import annotations

import html
import os
import re
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import persistence

router = APIRouter(prefix="/api", tags=["contact"])

_RESEND_ENDPOINT = "https://api.resend.com/emails"
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Human-readable topic labels for the three site CTAs.
_TOPICS = {
    "early-access": "Early access request",
    "partnership": "OEM partnership",
    "general": "General inquiry",
}

# Lightweight in-memory throttle: max 3 submissions per IP per rolling window.
_WINDOW_S = 300
_MAX_PER_WINDOW = 3
_hits: dict[str, list[float]] = {}


class ContactPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=200)
    topic: str = Field(default="general", max_length=40)
    message: str = Field(default="", max_length=4000)
    # Honeypot — real users never fill this hidden field; bots do.
    company_url: str = Field(default="", max_length=200)


def _from_addr() -> str:
    return os.getenv("ORBITAL_CONTACT_FROM", "Orbital AI <hello@orbital-ai.io>")


def _to_addr() -> str:
    return os.getenv("ORBITAL_CONTACT_TO", "hello@orbital-ai.io")


def _throttled(ip: str) -> bool:
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < _WINDOW_S]
    recent.append(now)
    _hits[ip] = recent
    return len(recent) > _MAX_PER_WINDOW


async def _send(api_key: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            _RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )
        resp.raise_for_status()


@router.post("/contact")
async def contact(payload: ContactPayload, request: Request) -> JSONResponse:
    # Silently accept honeypot hits so bots get a 200 and don't retry, but never send.
    if payload.company_url.strip():
        return JSONResponse({"ok": True})

    email = payload.email.strip()
    if not _EMAIL_RE.match(email):
        return JSONResponse({"ok": False, "error": "Please enter a valid email address."}, status_code=422)

    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    if _throttled(ip):
        return JSONResponse(
            {"ok": False, "error": "Too many requests — please try again in a few minutes."},
            status_code=429,
        )

    api_key = os.getenv("RESEND_API_KEY", "").strip()

    topic_label = _TOPICS.get(payload.topic, "General inquiry")
    name = payload.name.strip()
    msg = (payload.message or "").strip() or "(no message provided)"
    safe_name, safe_msg = html.escape(name), html.escape(msg)

    # Capture the lead first — it lands in the operator inbox (same place inbound mail to
    # @orbital-ai.io lands). This is the durable record; email delivery below is a courtesy.
    persisted = persistence.save_inbox_message(
        msg_id=f"form-{uuid.uuid4().hex}",
        received_at=datetime.now(timezone.utc).isoformat(),
        source="form",
        from_addr=f"{name} <{email}>",
        to_addr=_to_addr(),
        subject=f"{topic_label} — {name}",
        body=msg,
    )

    # If we can't persist (no DB, e.g. local dev) and can't email, don't silently drop it.
    if not persisted and not api_key:
        return JSONResponse(
            {"ok": False, "error": "Contact is temporarily unavailable. Please email hello@orbital-ai.io."},
            status_code=503,
        )

    # Courtesy auto-reply — best-effort; a failure here must not fail the submission.
    reply_from = os.getenv("ORBITAL_CONTACT_REPLY", _from_addr())
    ack_html = (
        f"<div style=\"font-family:system-ui,sans-serif;font-size:14px;color:#0a0d14;line-height:1.6\">"
        f"<p>Hi {safe_name},</p>"
        f"<p>Thanks for reaching out to <strong>Orbital AI</strong> — the operational layer for deployed robots. "
        f"We've received your {html.escape(topic_label.lower())} and someone on the team will be in touch shortly.</p>"
        f"<p>In the meantime, the live fleet-control demo is running at "
        f"<a href=\"https://orbital-ai.io/app/\">orbital-ai.io/app</a>.</p>"
        f"<p style=\"color:#828c9b\">— The Orbital AI team</p></div>"
    )
    if api_key:
        try:
            await _send(api_key, {
                "from": reply_from,
                "to": [email],
                "subject": "We got your message — Orbital AI",
                "html": ack_html,
            })
        except Exception:
            pass

    return JSONResponse({"ok": True})
