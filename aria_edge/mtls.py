"""Optional mTLS client material for edge → cloud (Sprint C3).

Env (all optional; unset = plain HTTPS/HTTP as today):
  ORBITAL_MTLS_CERT   — client certificate PEM path
  ORBITAL_MTLS_KEY    — client private key PEM path
  ORBITAL_MTLS_CA     — CA bundle used to verify the cloud server
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MtlsConfig:
    cert: str
    key: str
    ca: str | None = None

    def httpx_kwargs(self) -> dict[str, Any]:
        kw: dict[str, Any] = {"cert": (self.cert, self.key)}
        if self.ca:
            kw["verify"] = self.ca
        return kw


def load_mtls_config() -> MtlsConfig | None:
    cert = (os.getenv("ORBITAL_MTLS_CERT") or "").strip()
    key = (os.getenv("ORBITAL_MTLS_KEY") or "").strip()
    if not cert or not key:
        return None
    ca = (os.getenv("ORBITAL_MTLS_CA") or "").strip() or None
    return MtlsConfig(cert=cert, key=key, ca=ca)
