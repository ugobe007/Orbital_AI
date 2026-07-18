#!/usr/bin/env bash
# Reset Orbital Fly RBAC secrets — VALUES ONLY, no quote characters stored.
#
# Usage:
#   ./scripts/set_fly_rbac_secrets.sh           # set + print tokens (staged)
#   ./scripts/set_fly_rbac_secrets.sh --deploy  # set + fly secrets deploy
#
# IMPORTANT:
#   - Do NOT paste quotation marks into the Fly dashboard value fields.
#   - In bash, quotes around an argument are shell syntax only; Fly receives
#     the characters inside. This script never puts " into the secret value.
#   - The app reads ORBITAL_RBAC_TOKENS only (not ADMIN_TOK / OPS_TOK / VIEW_TOK).

set -euo pipefail

APP="${FLY_APP:-orbital-ai}"
DEPLOY=0
if [[ "${1:-}" == "--deploy" ]]; then
  DEPLOY=1
fi

ADMIN_TOK="$(openssl rand -hex 24)"
OPS_TOK="$(openssl rand -hex 24)"
VIEW_TOK="$(openssl rand -hex 24)"

# Raw value — no surrounding quotes in the string itself.
RBAC_TOKENS="admin:${ADMIN_TOK},operator:${OPS_TOK},viewer:${VIEW_TOK}"

echo "════════════════════════════════════════════════════════════"
echo "  SAVE THESE TOKENS NOW — they will not be shown again"
echo "════════════════════════════════════════════════════════════"
echo "  admin    Bearer token:  ${ADMIN_TOK}"
echo "  operator Bearer token:  ${OPS_TOK}"
echo "  viewer   Bearer token:  ${VIEW_TOK}"
echo "────────────────────────────────────────────────────────────"
echo "  ORBITAL_RBAC_TOKENS (exact stored value, no quotes):"
echo "  ${RBAC_TOKENS}"
echo "════════════════════════════════════════════════════════════"
echo

echo "Unsetting unused secrets ADMIN_TOK OPS_TOK VIEW_TOK (if present)…"
fly secrets unset ADMIN_TOK OPS_TOK VIEW_TOK -a "$APP" 2>/dev/null || true

echo "Setting ORBITAL_RBAC_ENFORCE=1 ORBITAL_STRICT_OEM_SCOPES=1 …"
# Digit only — no quotes in the value.
fly secrets set -a "$APP" ORBITAL_RBAC_ENFORCE=1 ORBITAL_STRICT_OEM_SCOPES=1

echo "Setting ORBITAL_RBAC_TOKENS (value has no quotation marks)…"
# Assignment form: Fly gets everything after = with no " characters.
fly secrets set -a "$APP" "ORBITAL_RBAC_TOKENS=${RBAC_TOKENS}"

echo
echo "Staged secrets:"
fly secrets list -a "$APP"

if [[ "$DEPLOY" -eq 1 ]]; then
  echo
  echo "WARNING: Deploying RBAC will 401 anonymous /app and the marketing embed"
  echo "until the UI sends a Bearer token. Continuing in 3s…"
  sleep 3
  fly secrets deploy -a "$APP"
  echo "Deployed."
else
  echo
  echo "Secrets are STAGED (not live yet)."
  echo "When ready to apply:  fly secrets deploy -a ${APP}"
  echo "Or re-run:            $0 --deploy"
fi
