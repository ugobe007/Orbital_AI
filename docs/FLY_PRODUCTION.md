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

# Shell double-quotes are NOT stored in the secret — they only protect the string.
# Correct stored value looks like:  admin:abc...,operator:def...,viewer:ghi...
# Wrong:  "admin:abc..."   or   ""admin:abc...""  (literal quote characters)
fly secrets set -a orbital-ai \
  ORBITAL_RBAC_ENFORCE=1 \
  ORBITAL_STRICT_OEM_SCOPES=1 \
  ORBITAL_RBAC_TOKENS="admin:${ADMIN_TOK},operator:${OPS_TOK},viewer:${VIEW_TOK}"

# Apply staged secrets (required — set alone does not restart the app):
fly secrets deploy -a orbital-ai
```

| Variable | Value |
|----------|--------|
| `ORBITAL_RBAC_ENFORCE` | `1` (literal digit, no quotes) |
| `ORBITAL_STRICT_OEM_SCOPES` | `1` |
| `ORBITAL_RBAC_TOKENS` | One string: `admin:<hex>,operator:<hex>,viewer:<hex>` — **not** three separate Fly secrets |

Do **not** create Fly secrets named `ADMIN_TOK` / `OPS_TOK` / `VIEW_TOK`. Those are only local shell variables. The app reads **`ORBITAL_RBAC_TOKENS` only**.

**Public demo warning:** With `ORBITAL_RBAC_ENFORCE=1`, anonymous `/app/` and the marketing embed get `401` on fleet/map unless the UI sends a Bearer token. Keep RBAC off for the open simulator, or wait until the embed carries a viewer token.

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
