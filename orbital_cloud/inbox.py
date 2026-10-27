"""Inbound email + operator inbox.

`orbital-ai.io` has Resend receiving enabled, so mail to any address on the domain
(hello@, partners@, …) is POSTed here as an ``email.received`` webhook. We verify the
Svix signature, persist the message, and expose a token-guarded operator inbox — a real,
self-contained @orbital-ai.io mailbox surfaced inside Orbital rather than a third-party
client. Contact-form submissions land in the same inbox.

Configuration (env):
  RESEND_WEBHOOK_SECRET — Svix signing secret (``whsec_…``). If unset, signatures are not
                          verified (dev only). Set it in production.
  ORBITAL_ADMIN_TOKEN   — bearer token required to read the inbox. If unset, the inbox API
                          returns 503 (locked) rather than leaking messages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html as html_lib
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from . import persistence

router = APIRouter(tags=["inbox"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _admin_ok(request: Request) -> bool:
    token = os.getenv("ORBITAL_ADMIN_TOKEN", "").strip()
    if not token:
        return False
    supplied = (
        request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        or request.query_params.get("token", "").strip()
    )
    return bool(supplied) and hmac.compare_digest(supplied, token)


def _verify_svix(secret: str, headers, body: bytes) -> bool:
    svix_id = headers.get("svix-id") or headers.get("webhook-id")
    svix_ts = headers.get("svix-timestamp") or headers.get("webhook-timestamp")
    svix_sig = headers.get("svix-signature") or headers.get("webhook-signature")
    if not (svix_id and svix_ts and svix_sig):
        return False
    key = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        secret_bytes = base64.b64decode(key)
    except Exception:
        return False
    signed = f"{svix_id}.{svix_ts}.{body.decode('utf-8', 'replace')}".encode()
    expected = base64.b64encode(hmac.new(secret_bytes, signed, hashlib.sha256).digest()).decode()
    for part in svix_sig.split(" "):
        sig = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(sig, expected):
            return True
    return False


@router.post("/api/inbound/resend")
async def inbound_resend(request: Request) -> JSONResponse:
    body = await request.body()
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "").strip()
    if secret and not _verify_svix(secret, request.headers, body):
        return JSONResponse({"ok": False, "error": "invalid signature"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "error": "invalid json"}, status_code=400)

    if payload.get("type") != "email.received":
        # Acknowledge non-inbound events (delivery/bounce/etc.) without storing them.
        return JSONResponse({"ok": True, "ignored": payload.get("type")})

    data = payload.get("data") or {}
    to_field = data.get("to")
    if isinstance(to_field, list):
        to_addr = ", ".join(str(t) for t in to_field)
    else:
        to_addr = str(to_field or "")
    from_addr = str(data.get("from") or "")
    subject = str(data.get("subject") or "(no subject)")
    text_body = data.get("text") or ""
    if not text_body and data.get("html"):
        text_body = data["html"]
    msg_id = str(data.get("email_id") or data.get("id") or uuid.uuid4().hex)

    persistence.save_inbox_message(
        msg_id=msg_id, received_at=_now_iso(), source="inbound",
        from_addr=from_addr, to_addr=to_addr, subject=subject, body=str(text_body),
    )
    return JSONResponse({"ok": True})


@router.get("/api/inbox")
async def inbox_list(request: Request) -> JSONResponse:
    if not os.getenv("ORBITAL_ADMIN_TOKEN", "").strip():
        return JSONResponse({"ok": False, "error": "inbox locked — ORBITAL_ADMIN_TOKEN not set"}, status_code=503)
    if not _admin_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    messages = persistence.load_inbox_messages(limit=200)
    return JSONResponse({"ok": True, "unread": persistence.inbox_unread_count(), "messages": messages})


@router.post("/api/inbox/{msg_id}/read")
async def inbox_mark_read(msg_id: str, request: Request) -> JSONResponse:
    if not _admin_ok(request):
        return JSONResponse({"ok": False, "error": "unauthorized"}, status_code=401)
    persistence.mark_inbox_read(msg_id)
    return JSONResponse({"ok": True})


_INBOX_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Orbital AI · Inbox</title><link rel="icon" type="image/svg+xml" href="/assets/favicon.svg" />
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@800;900&amp;family=IBM+Plex+Mono:wght@400;500;600&amp;family=IBM+Plex+Sans:wght@400;500;600&amp;display=swap">
<style>
  :root{--ground:#EEF0EC;--ink:#111418;--line:#C9CCC6;--accent:#1D4ED8;--mut:#4A4F57;}
  *{box-sizing:border-box}
  body{margin:0;background:var(--ground);color:var(--ink);font-family:'IBM Plex Sans',ui-sans-serif,system-ui,sans-serif}
  header{position:sticky;top:0;background:var(--ground);border-bottom:2px solid var(--ink);padding:16px 24px;display:flex;align-items:center;gap:10px}
  .title{font-family:Archivo,sans-serif;font-weight:900;font-size:20px;letter-spacing:-.03em}.title .a{color:var(--accent)}
  .title .s{font-family:'IBM Plex Mono',monospace;font-weight:500;font-size:12px;letter-spacing:.12em;color:var(--mut);margin-left:10px}
  .wrap{max-width:1000px;margin:0 auto;padding:24px}
  .bar{display:flex;gap:10px;align-items:center;margin-bottom:20px}
  input{background:#fff;border:1px solid var(--line);color:var(--ink);padding:0 12px;min-height:44px;font:inherit;font-size:15px;flex:1;border-radius:0}
  input:focus{outline:2px solid var(--accent);outline-offset:-1px}
  button{background:var(--accent);border:0;color:#fff;min-height:44px;padding:0 18px;font:inherit;font-size:15px;font-weight:600;cursor:pointer;border-radius:0}
  .msg{border:1px solid var(--line);background:#fff;padding:18px;margin-bottom:12px}
  .msg.unread{border-left:3px solid var(--accent)}
  .row{display:flex;justify-content:space-between;gap:12px;align-items:baseline;flex-wrap:wrap}
  .subj{font-weight:600;font-size:16px}
  .meta{font-size:12px;color:var(--mut);font-family:'IBM Plex Mono',ui-monospace,monospace}
  .tag{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;padding:2px 7px;background:var(--ink);color:#fff}
  .tag.inbound{background:var(--accent)}
  .tag.form{background:#15803D}
  .from{font-size:13px;color:var(--mut);margin-top:4px}
  .body{margin-top:10px;white-space:pre-wrap;font-size:14px;color:#30353C;line-height:1.55}
  .empty{color:var(--mut);text-align:center;padding:60px 0}
  .err{color:#B91C1C;font-size:14px;margin-bottom:12px}
</style></head>
<body>
<header><svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true"><circle cx="11" cy="11" r="9.5" fill="none" stroke="#111418" stroke-width="2"/><circle cx="11" cy="11" r="3.5" fill="#1D4ED8"/><rect x="16.5" y="2.5" width="4" height="4" fill="#111418"/></svg><div class="title">Orbital<span class="a"> AI</span><span class="s">INBOX</span></div></header>
<div class="wrap">
  <div class="bar">
    <input id="tok" type="password" placeholder="Admin token" />
    <button id="load">Open inbox</button>
  </div>
  <div id="err" class="err" style="display:none"></div>
  <div id="list"></div>
</div>
<script>
  const tokEl=document.getElementById('tok'),listEl=document.getElementById('list'),errEl=document.getElementById('err');
  tokEl.value=localStorage.getItem('orbital_admin_token')||'';
  function esc(s){const d=document.createElement('div');d.textContent=s||'';return d.innerHTML;}
  function tag(src){const c=src==='form'?'form':(src==='inbound'?'inbound':'');return '<span class="tag '+c+'">'+esc(src)+'</span>';}
  async function load(){
    const t=tokEl.value.trim();errEl.style.display='none';
    if(!t){errEl.textContent='Enter the admin token.';errEl.style.display='block';return;}
    localStorage.setItem('orbital_admin_token',t);
    let r;try{r=await fetch('/api/inbox',{headers:{Authorization:'Bearer '+t}});}catch(e){errEl.textContent='Network error.';errEl.style.display='block';return;}
    const b=await r.json().catch(()=>({}));
    if(!r.ok||!b.ok){errEl.textContent=b.error||('HTTP '+r.status);errEl.style.display='block';return;}
    if(!b.messages.length){listEl.innerHTML='<div class="empty">No messages yet.</div>';return;}
    listEl.innerHTML=b.messages.map(m=>(
      '<div class="msg '+(m.read?'':'unread')+'">'+
      '<div class="row"><div class="subj">'+esc(m.subject)+'</div>'+
      '<div class="meta">'+tag(m.source)+' &nbsp;'+esc((m.received_at||'').replace('T',' ').slice(0,16))+'</div></div>'+
      '<div class="from">'+esc(m.from_addr)+' &rarr; '+esc(m.to_addr)+'</div>'+
      '<div class="body">'+esc(m.body)+'</div></div>'
    )).join('');
  }
  document.getElementById('load').addEventListener('click',load);
  tokEl.addEventListener('keydown',e=>{if(e.key==='Enter')load();});
  if(tokEl.value)load();
</script>
</body></html>"""


@router.get("/inbox", include_in_schema=False)
async def inbox_page() -> HTMLResponse:
    return HTMLResponse(_INBOX_HTML)
