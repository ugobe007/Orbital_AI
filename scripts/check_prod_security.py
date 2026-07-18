#!/usr/bin/env python3
"""Warn when production security flags are off (CI / pre-deploy smoke).

Exit 0 always unless ``--strict`` (then exit 1 when RBAC or strict OEM scopes off).
Never prints secret values.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orbital_cloud.secrets import prod_security_flags  # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 1 if ORBITAL_RBAC_ENFORCE or ORBITAL_STRICT_OEM_SCOPES is off",
    )
    args = p.parse_args()
    flags = prod_security_flags()
    print("Orbital production security flags:")
    for k, v in flags.items():
        print(f"  {k}: {'ON' if v else 'OFF'}")

    missing = []
    if not flags["rbac_enforce"]:
        missing.append("ORBITAL_RBAC_ENFORCE=1")
    if not flags["strict_oem_scopes"]:
        missing.append("ORBITAL_STRICT_OEM_SCOPES=1")
    if flags["rbac_enforce"] and not flags["rbac_tokens_configured"]:
        missing.append("ORBITAL_RBAC_TOKENS=admin:…,operator:…,viewer:…")

    if missing:
        print("Recommended for production:")
        for m in missing:
            print(f"  - {m}")
        if args.strict and (
            not flags["rbac_enforce"] or not flags["strict_oem_scopes"]
        ):
            return 1
    else:
        print("All checked flags look production-ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
