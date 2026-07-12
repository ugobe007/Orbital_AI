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

from . import persistence
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
        self._load_from_db()

    # ── Persistence (no-op unless ORBITAL_DB_PATH is set) ────────────────────────
    def _load_from_db(self) -> None:
        persistence.init_db()
        for oem_id, key_hash, data in persistence.load_oems():
            try:
                partner = OEMPartner.model_validate_json(data)
            except Exception:  # noqa: BLE001 — skip a corrupt row rather than crash boot
                continue
            self._by_id[oem_id] = _OEMRecord(partner, key_hash)
            self._id_by_hash[key_hash] = oem_id

    def _persist(self, oem_id: str) -> None:
        rec = self._by_id.get(oem_id)
        if rec is not None:
            persistence.save_oem(oem_id, rec.key_hash, rec.partner.model_dump_json())

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
            self._persist(oem_id)
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
            self._persist(oem_id)
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
            self._persist(oem_id)
            return rec.partner

    def set_status(self, oem_id: str, status: OEMStatus) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            if rec is None:
                return None
            rec.partner.status = status
            rec.partner.updated_at = time.time()
            self._persist(oem_id)
            return rec.partner

    # ── Reads ────────────────────────────────────────────────────────────────────
    def get(self, oem_id: str) -> Optional[OEMPartner]:
        with self._lock:
            rec = self._by_id.get(oem_id)
            return rec.partner if rec else None

    def list(self) -> list[OEMPartner]:
        with self._lock:
            return [rec.partner for rec in self._by_id.values()]

    # ── Demo seeding ─────────────────────────────────────────────────────────────
    def seed_demo(self, specs: list[tuple[str, str, "ControlTransport", list[APIScope]]]) -> None:
        """Register demo OEM partners with varied grant levels (idempotent per vendor).

        Each spec is (company_name, vendor, transport, scopes). Scopes outside the vendor's
        capability ceiling are dropped by ``grant_scopes``, so the seeded readiness reflects
        each protocol's real limits (e.g. Boston Dynamics can't be granted velocity)."""
        for company_name, vendor, transport, scopes in specs:
            if self.partner_for_vendor(vendor) is not None:
                continue
            partner, _ = self.register(OEMRegisterIn(
                company_name=company_name, vendor=vendor,
                contact_email=f"partners@{vendor.lower().replace(' ', '')}.example", transport=transport,
            ))
            if scopes:
                self.grant_scopes(partner.id, scopes)

    # ── Vendor → grants resolution (powers control-path scope enforcement) ───────
    def partner_for_vendor(self, vendor: str) -> Optional[OEMPartner]:
        """The active/most-recently-updated partner registered for a vendor, if any.

        Robots carry a vendor, not an OEM id, so control enforcement resolves the owning
        partner by vendor (case-insensitive). Prefers an ACTIVE partner over a pending one.
        """
        v = (vendor or "").strip().lower()
        with self._lock:
            matches = [r.partner for r in self._by_id.values() if r.partner.vendor.strip().lower() == v]
        if not matches:
            return None
        active = [p for p in matches if p.status == OEMStatus.ACTIVE]
        pool = active or matches
        return max(pool, key=lambda p: p.updated_at)

    def granted_scopes_for_vendor(self, vendor: str) -> Optional[set[APIScope]]:
        """Granted scopes for a vendor's OEM, or None when the vendor is *unmanaged*
        (no OEM registered) — the caller decides how to treat unmanaged robots."""
        partner = self.partner_for_vendor(vendor)
        if partner is None:
            return None
        if partner.status == OEMStatus.SUSPENDED:
            return set()
        return set(partner.granted_scopes)

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
