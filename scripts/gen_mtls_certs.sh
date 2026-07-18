#!/usr/bin/env bash
# Generate a local mTLS CA + server + client certs for edge↔cloud tests (Sprint C3).
# Usage: ./scripts/gen_mtls_certs.sh [outdir]
# Default outdir: scripts/mtls/certs (gitignored)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$ROOT/mtls/certs}"
mkdir -p "$OUT"
cd "$OUT"

openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.crt -days 3650 \
  -subj "/CN=Orbital AI Test CA" 2>/dev/null

openssl req -newkey rsa:2048 -nodes -keyout server.key -out server.csr \
  -subj "/CN=localhost" 2>/dev/null
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out server.crt -days 825 2>/dev/null

openssl req -newkey rsa:2048 -nodes -keyout client.key -out client.csr \
  -subj "/CN=aria-edge" 2>/dev/null
openssl x509 -req -in client.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -out client.crt -days 825 2>/dev/null

rm -f server.csr client.csr ca.srl
echo "Wrote mTLS material to $OUT"
ls -1 "$OUT"
