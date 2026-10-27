#!/usr/bin/env python3
"""Static smoke checks for the marketing page and fleet dashboard.

The public homepage is a single static page (site/index.html, inline styles and
SVGs) whose pilot form is wired to /api/contact by site.js. The fleet console is
dashboard/index.html, served at /app/.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "index.html"
DASHBOARD = ROOT / "dashboard" / "index.html"
TW_CSS = ROOT / "dashboard" / "tw.css"
ASSETS = ROOT / "site" / "assets"
PHOTOS = ROOT / "dashboard" / "orbital-ai-site"

REQUIRED_ASSETS = ("site.js", "favicon.svg")
REQUIRED_ANCHORS = ("platform", "digital-net", "pilot")
FORM_FIELDS = ("name", "email", "topic", "website", "message", "company_url")
REQUIRED_PHOTOS = (
    "hero-warehouse.jpg",
    "humanoid-deployment.png",
    "flywheel-figure-bmw.jpg",
    "stack-spot-posco.jpg",
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if not SITE.is_file():
        fail(f"missing {SITE.relative_to(ROOT)}")
    if not DASHBOARD.is_file():
        fail(f"missing {DASHBOARD.relative_to(ROOT)}")
    if not TW_CSS.is_file() or TW_CSS.stat().st_size < 1000:
        fail("missing or empty dashboard/tw.css — run: npm run build:css")

    html = SITE.read_text(encoding="utf-8")
    dash = DASHBOARD.read_text(encoding="utf-8")

    for anchor in REQUIRED_ANCHORS:
        if f'id="{anchor}"' not in html or f'href="#{anchor}"' not in html:
            fail(f"marketing page needs section #{anchor} and a nav link to it")
    if 'id="contact-form"' not in html:
        fail("marketing page needs the inline pilot form (#contact-form)")
    for field in FORM_FIELDS:
        if f'name="{field}"' not in html:
            fail(f"pilot form is missing the {field!r} field /api/contact expects")
    for name in REQUIRED_ASSETS:
        if f"/assets/{name}" not in html:
            fail(f"marketing page does not reference /assets/{name}")
        path = ASSETS / name
        if not path.is_file() or path.stat().st_size < 100:
            fail(f"missing or empty site/assets/{name}")

    for name in REQUIRED_PHOTOS:
        path = PHOTOS / name
        if not path.is_file() or path.stat().st_size < 1000:
            fail(f"missing or empty dashboard/orbital-ai-site/{name}")

    if "cdn.tailwindcss.com" in html or "cdn.tailwindcss.com" in dash:
        fail("Tailwind CDN is forbidden — use built /tw.css")
    if "/tw.css" not in dash:
        fail("dashboard must link /tw.css")

    print(
        f"OK: site smoke passed "
        f"(assets={','.join(REQUIRED_ASSETS)}, tw.css={TW_CSS.stat().st_size}B)"
    )


if __name__ == "__main__":
    main()
