"""OEM Onboarding API — 3rd-party robot companies join Orbital AI and unlock parts of
their robot API to us.

Flow:
  1. POST /api/oem/register            → create a partner, return an API key (shown once).
  2. POST /api/oem/{id}/scopes         → OEM unlocks scopes (telemetry/control/…), auth'd
                                          with their API key. Bounded by their protocol's
                                          capability ceiling.
  3. GET  /api/oem/{id}/profile        → what Orbital can now do (monitor/control readiness).
  4. DELETE /api/oem/{id}/scopes       → OEM revokes access at any time.

Operator/read endpoints (GET list, GET one) are open in this v0 scaffold; in production
they sit behind operator auth. OEM-scoped writes require the partner's bearer key.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException

from ..models import (
    IntegrationProfile,
    OEMPartner,
    OEMRegisterIn,
    OEMRegistered,
    ScopeGrantIn,
)
from ..oem_store import oem_store

router = APIRouter(prefix="/api/oem", tags=["oem"])


def _require_partner(oem_id: str, authorization: Optional[str]) -> OEMPartner:
    """Authenticate a partner by bearer key and confirm it matches the path oem_id."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer API key")
    api_key = authorization.split(" ", 1)[1].strip()
    partner = oem_store.authenticate(api_key)
    if partner is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    if partner.id != oem_id:
        raise HTTPException(status_code=403, detail="API key does not match this OEM")
    return partner


@router.post("/register", response_model=OEMRegistered, status_code=201)
async def register_oem(body: OEMRegisterIn) -> OEMRegistered:
    partner, credential = oem_store.register(body)
    return OEMRegistered(partner=partner, credential=credential)


@router.post("/{oem_id}/scopes", response_model=OEMPartner)
async def grant_scopes(
    oem_id: str,
    body: ScopeGrantIn,
    authorization: Optional[str] = Header(default=None),
) -> OEMPartner:
    _require_partner(oem_id, authorization)
    partner = oem_store.grant_scopes(oem_id, body.scopes)
    if partner is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return partner


@router.delete("/{oem_id}/scopes", response_model=OEMPartner)
async def revoke_scopes(
    oem_id: str,
    body: ScopeGrantIn,
    authorization: Optional[str] = Header(default=None),
) -> OEMPartner:
    _require_partner(oem_id, authorization)
    partner = oem_store.revoke_scopes(oem_id, body.scopes)
    if partner is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return partner


@router.get("/{oem_id}/profile", response_model=IntegrationProfile)
async def integration_profile(oem_id: str) -> IntegrationProfile:
    profile = oem_store.profile(oem_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return profile


@router.get("/{oem_id}", response_model=OEMPartner)
async def get_oem(oem_id: str) -> OEMPartner:
    partner = oem_store.get(oem_id)
    if partner is None:
        raise HTTPException(status_code=404, detail="OEM not found")
    return partner


@router.get("", response_model=list[OEMPartner])
async def list_oems() -> list[OEMPartner]:
    return oem_store.list()
