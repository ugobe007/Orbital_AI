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
<title>Orbital AI · Inbox</title><link rel="icon" href="/orbital-logo.png" />
<style>
  :root{--line:#242c3a;--azure:#00a5da;--brand:#00be7d;}
  *{box-sizing:border-box}
  body{margin:0;background:#0a0d14;color:#e6eaef;font-family:'Space Grotesk',ui-sans-serif,system-ui,-apple-system,sans-serif}
  header{position:sticky;top:0;background:rgba(10,13,20,.9);backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:14px 20px;display:flex;align-items:center;gap:12px}
  header img{width:28px;height:28px;object-fit:contain}
  .title{font-weight:600}.title .a{color:var(--azure);font-weight:300}
  .wrap{max-width:1000px;margin:0 auto;padding:20px}
  .bar{display:flex;gap:10px;align-items:center;margin-bottom:16px}
  input{background:#0c0f17;border:1px solid var(--line);color:#e6eaef;border-radius:8px;padding:9px 12px;font-size:14px;flex:1;outline:none}
  button{background:#0e2230;border:1px solid rgba(0,165,218,.6);color:#bfeaff;border-radius:8px;padding:9px 14px;font-size:14px;font-weight:600;cursor:pointer}
  .msg{border:1px solid var(--line);border-radius:12px;background:#0c0f17;padding:16px;margin-bottom:12px}
  .msg.unread{border-color:rgba(0,165,218,.5)}
  .row{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
  .subj{font-weight:600;font-size:15px}
  .meta{font-size:12px;color:#828c9b;font-family:'JetBrains Mono',ui-monospace,monospace}
  .tag{font-size:10.5px;text-transform:uppercase;letter-spacing:.12em;padding:2px 8px;border-radius:999px;border:1px solid var(--line);color:#aab4c1}
  .tag.inbound{color:#7fd6f2;border-color:rgba(0,165,218,.4)}
  .tag.form{color:#00be7d;border-color:rgba(0,190,125,.4)}
  .from{font-size:13px;color:#aab4c1;margin-top:4px}
  .body{margin-top:10px;white-space:pre-wrap;font-size:13.5px;color:#cfd6df;line-height:1.55}
  .empty{color:#828c9b;text-align:center;padding:60px 0}
  .err{color:#ff7a7a;font-size:13px;margin-bottom:12px}
</style></head>
<body>
<header><img src="/orbital-logo.png" alt="" /><div class="title">Orbital<span class="a"> AI</span> · Inbox</div></header>
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
