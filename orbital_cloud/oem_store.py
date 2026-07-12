"""OEM onboarding state — registrations, granted scopes, and API credentials.

In-memory for v0 (same pattern as ``store.Store``); a real deployment swaps this for a DB
behind the same methods. API keys are never stored in the clear — we keep a SHA-256 hash and
a short non-secret prefix for display, and return the raw key exactly once at registration.

The scope an OEM may grant is bounded by their fleet adapter's capability ceiling
(``fleet_adapters.capability_ceiling_for``), so an OEM can't grant control we can't actually
exercise over their protocol.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
import uuid
from typing import Optional

from fleet_adapters import capability_ceiling_for

from .models import (
    APIScope,
    ControlTransport,
    IntegrationProfile,
    OEMCredential,
    OEMPartner,
    OEMRegisterIn,
    OEMStatus,
)


def _hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _ceiling_scopes(vendor: str) -> list[APIScope]:
    # Capability values are 1:1 with APIScope values by design.
    ceiling = capability_ceiling_for(vendor)
    return [APIScope(c.value) for c in ceiling]


class _OEMRecord:
    def __init__(self, partner: OEMPartner, key_hash: str) -> None:
        self.partner = partner
        self.key_hash = key_hash


class OEMStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_id: dict[str, _OEMRecord] = {}
        self._id_by_hash: dict[str, str] = {}

    # ── Registration ────────────────────────────────────────────────────────────
    def register(self, body: OEMRegisterIn) -> tuple[OEMPartner, OEMCredential]:
        with self._lock:
            now = time.time()
            oem_id = f"oem-{uuid.uuid4().hex[:10]}"
            raw_key, prefix, key_hash = _mint_api_key()
            partner = OEMPartner(
                id=oem_id,
                company_name=body.company_name,
                vendor=body.vendor,
                contact_email=body.contact_email,
                transport=body.transport,
                status=OEMStatus.PENDING,
                ceiling_scopes=_ceiling_scopes(body.vendor),
                granted_scopes=[],
                api_key_prefix=prefix,
                created_at=now,
                updated_at=now,
            )
            self._by_id[oem_id] = _OEMRecord(partner, key_hash)
            self._id_by_hash[key_hash] = oem_id
            return partner, OEMCredential(api_key=raw_key, key_prefix=prefix)

    # ── Auth ────────────────────────────────────────────────────────────────────
    def authenticate(self, api_key: str) -> Optional[OEMPartner]:
        with self._lock:
            oem_id = self._id_by_hash.get(_hash_key(api_key.strip()))
            rec = self._by_id.get(oem_id) if oem_id else None
            return rec.partner if rec else None

    # ── Scope management (the "unlock parts of their API" step) ──────────────────
    def grant_scopes(self, oem_id: str, scopes: list[APIScope]) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            if rec is None:
                return None
            ceiling = set(rec.partner.ceiling_scopes)
            # Only scopes the OEM's protocol actually supports can be unlocked.
            allowed = [s for s in scopes if s in ceiling]
            merged = list(dict.fromkeys([*rec.partner.granted_scopes, *allowed]))
            rec.partner.granted_scopes = merged
            if merged and rec.partner.status == OEMStatus.PENDING:
                rec.partner.status = OEMStatus.ACTIVE
            rec.partner.updated_at = time.time()
            return rec.partner

    def revoke_scopes(self, oem_id: str, scopes: list[APIScope]) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            if rec is None:
                return None
            drop = set(scopes)
            rec.partner.granted_scopes = [s for s in rec.partner.granted_scopes if s not in drop]
            if not rec.partner.granted_scopes and rec.partner.status == OEMStatus.ACTIVE:
                rec.partner.status = OEMStatus.PENDING
            rec.partner.updated_at = time.time()
            return rec.partner

    def set_status(self, oem_id: str, status: OEMStatus) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            if rec is None:
                return None
            rec.partner.status = status
            rec.partner.updated_at = time.time()
            return rec.partner

    # ── Reads ────────────────────────────────────────────────────────────────────
    def get(self, oem_id: str) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            return rec.partner if rec else None

    def list(self) -> list[OEMPartner]:
        with self._lock:
            return [rec.partner for rec in self._by_id.values()]

    def profile(self, oem_id: str) -> Optional[IntegrationProfile]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            if rec is None:
                return None
            p = rec.partner
            granted = set(p.granted_scopes)
            ceiling = set(p.ceiling_scopes)
            return IntegrationProfile(
                oem_id=p.id,
                company_name=p.company_name,
                vendor=p.vendor,
                transport=p.transport,
                status=p.status,
                ceiling_scopes=p.ceiling_scopes,
                granted_scopes=p.granted_scopes,
                missing_scopes=[s for s in ceiling - granted],
                control_ready=(p.status == OEMStatus.ACTIVE
                               and {APIScope.VELOCITY, APIScope.ESTOP} <= granted),
                monitor_ready=(p.status == OEMStatus.ACTIVE and APIScope.TELEMETRY in granted),
            )


def _mint_api_key() -> tuple[str, str, str]:
    """Return (raw_key, public_prefix, sha256_hash). Raw key is shown only once."""
    prefix = secrets.token_hex(4)          # 8 hex chars, safe to display
    secret = secrets.token_urlsafe(32)
    raw = f"orb_{prefix}_{secret}"
    return raw, prefix, _hash_key(raw)


# Process-wide singleton.
oem_store = OEMStore()
