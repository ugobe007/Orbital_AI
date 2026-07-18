# mTLS test certificates (Sprint C3)

Generate with:

```bash
./scripts/gen_mtls_certs.sh
```

Output lands in `scripts/mtls/certs/` (gitignored). Point the edge at them:

```bash
export ORBITAL_MTLS_CERT=scripts/mtls/certs/client.crt
export ORBITAL_MTLS_KEY=scripts/mtls/certs/client.key
export ORBITAL_MTLS_CA=scripts/mtls/certs/ca.crt
export ORBITAL_CLOUD_URL=https://localhost:8443
```

Cloud server TLS termination (nginx / Fly / local uvicorn with `--ssl-keyfile`) must present a cert signed by the same CA and require client certificates.
