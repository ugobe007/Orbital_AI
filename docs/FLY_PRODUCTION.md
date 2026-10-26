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

# DO NOT INCLUDE quotation marks in the secret VALUE.
# Wrong (literal " stored):   "admin:abc..."     or    ""1""
# Correct:                    admin:abc...       or    1
#
# Preferred: use the reset script (never puts " into values):
#   ./scripts/set_fly_rbac_secrets.sh
#   ./scripts/set_fly_rbac_secrets.sh --deploy   # only when ready to lock public APIs

fly secrets set -a orbital-ai \
  ORBITAL_RBAC_ENFORCE=1 \
  ORBITAL_STRICT_OEM_SCOPES=1 \
  "ORBITAL_RBAC_TOKENS=admin:${ADMIN_TOK},operator:${OPS_TOK},viewer:${VIEW_TOK}"

fly secrets deploy -a orbital-ai
```

| Variable | Value |
|----------|--------|
| `ORBITAL_RBAC_ENFORCE` | `1` (literal digit, no quotes) |
| `ORBITAL_STRICT_OEM_SCOPES` | `1` |
| `ORBITAL_RBAC_TOKENS` | One string: `admin:<hex>,operator:<hex>,viewer:<hex>` — **not** three separate Fly secrets |

Do **not** create Fly secrets named `ADMIN_TOK` / `OPS_TOK` / `VIEW_TOK`. Those are only local shell variables. The app reads **`ORBITAL_RBAC_TOKENS` only**.

**Public demo:** With `ORBITAL_RBAC_ENFORCE=1`, anonymous callers are **Viewers** by default
(`ORBITAL_RBAC_ANON_VIEWER=1`). Fleet/map stay public; E-Stop / dispatch / OEM admin need Bearer tokens.
Set `ORBITAL_RBAC_ANON_VIEWER=0` only if you want every API to require a token.

**How clients authenticate after lockdown**

- Header: `Authorization: Bearer <token>` matching a role in `ORBITAL_RBAC_TOKENS`, **or**
- From the trusted proxy only (StageGate): `X-Orbital-Role: admin|operator|viewer|agent` **plus**
  `X-Orbital-Proxy-Secret: <ORBITAL_RBAC_PROXY_SECRET>`. Set the same secret on Orbital and on
  StageGate's proxy; keep it server-side — never send it from a browser.

Without the proxy secret, `X-Orbital-Role` can only *lower* a caller's role (e.g. an operator
token acting as viewer). Claiming a higher role returns 403. With `ORBITAL_RBAC_ENFORCE=0` the
header behaves as before (demo mode).

| Variable | Value |
|----------|--------|
| `ORBITAL_RBAC_PROXY_SECRET` | Long random string shared with StageGate's `/api/orbital/*` proxy (`openssl rand -hex 32`) |

**When control calls through the proxy get 403:** open `GET /api/dashboard/whoami` through the
same proxy (StageGate: `/api/orbital/whoami`) while signed in. It reports the resolved `role`,
whether the proxy sent a role and secret, and `proxy_secret_matches`. On a mismatch, compare
`proxy_secret_sent_fingerprint` with `proxy_secret_expected_fingerprint`; each is the first 8
hex characters of `printf %s "$SECRET" | shasum -a 256`, so you can tell which side holds the
stale value without revealing it. No `role_header` means the proxy did not recognise you as an
admin, or has no secret configured.

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
