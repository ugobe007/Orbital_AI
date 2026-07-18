# Fly production secrets — Orbital AI

**App:** `orbital-ai` · **Site:** https://orbital-ai.io  
**When to lock down:** Before treating the public dashboard as a production control surface.  
**Demo mode today:** Simulator + open APIs are fine without RBAC; robot OEM secrets belong on the **edge host**, not Fly.

---

## Already set (do not recreate)

| Secret | Purpose |
|--------|---------|
| `ORBITAL_CONTACT_FROM` | Contact form from-address |
| `RESEND_API_KEY` / `RESEND_WEBHOOK_SECRET` | Email |
| `ORBITAL_ADMIN_TOKEN` | Inbox API bearer |

`fly.toml` also sets `ORBITAL_DB_PATH` and `ORBITAL_SEED_OEMS` (non-secret env).

---

## Locking the dashboard (recommended when ready)

Generate three long random tokens (password manager or `openssl rand -hex 24`).

```bash
# Example values — replace with your own secrets before running
ADMIN_TOK="$(openssl rand -hex 24)"
OPS_TOK="$(openssl rand -hex 24)"
VIEW_TOK="$(openssl rand -hex 24)"

fly secrets set -a orbital-ai \
  ORBITAL_RBAC_ENFORCE=1 \
  ORBITAL_STRICT_OEM_SCOPES=1 \
  ORBITAL_RBAC_TOKENS="admin:${ADMIN_TOK},operator:${OPS_TOK},viewer:${VIEW_TOK}"
```

| Variable | Value |
|----------|--------|
| `ORBITAL_RBAC_ENFORCE` | `1` |
| `ORBITAL_STRICT_OEM_SCOPES` | `1` |
| `ORBITAL_RBAC_TOKENS` | `admin:<secret>,operator:<secret>,viewer:<secret>` |

**How clients authenticate after lockdown**

- Header: `Authorization: Bearer <token>` matching a role in `ORBITAL_RBAC_TOKENS`, **or**
- Header: `X-Orbital-Role: admin|operator|viewer` (role header alone is enough when tokens are not used — prefer Bearer tokens in production)

**Smoke (from a laptop with the same env exported, or after deploy):**

```bash
python3 scripts/check_prod_security.py --strict
```

**Warning:** Turning on `ORBITAL_STRICT_OEM_SCOPES=1` denies control to vendors with no OEM grant. Keep `ORBITAL_SEED_OEMS=1` (already in `fly.toml`) or register OEMs in the dashboard so the demo fleet stays controllable.

---

## Optional cloud secrets

| Variable | When |
|----------|------|
| `ORBITAL_CLOUD_API_KEY` | Edge nodes must authenticate to cloud APIs |
| `OPENAI_API_KEY` | Only if `ORBITAL_LLM_ENABLED=1` |

## Do **not** put these on Fly (edge / lab only)

| Variable | Why |
|----------|-----|
| `ORBITAL_SECRET_SPOT_JSON` | Spot credentials → edge host on VLAN 30 |
| `ORBITAL_SECRET_ARC_API_KEY` | Arc key → edge |
| Other `ORBITAL_SECRET_*` | Robot APIs must not be reachable from the public cloud VM |

---

## Deploy token (CI / laptop only)

Not an app secret — used by `fly deploy` / GitHub Actions:

- Laptop: `Orbital_AI/.env` → `FLY_API_TOKEN`
- GitHub: `ugobe007/Orbital_AI` secret `FLY_API_TOKEN`

Never put Orbital’s Fly token in StageGate `.env`.
