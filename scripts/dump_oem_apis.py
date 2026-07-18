#!/usr/bin/env python3
"""Print the OEM public API endpoint catalog (JSON)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fleet_adapters.oem_apis import list_oem_endpoints, known_oem_api_vendors


def main() -> None:
    catalog = list_oem_endpoints()
    print(json.dumps({
        "vendors": known_oem_api_vendors(),
        "endpoint_count": sum(len(v) for v in catalog.values()),
        "endpoints": catalog,
    }, indent=2))


if __name__ == "__main__":
    main()
