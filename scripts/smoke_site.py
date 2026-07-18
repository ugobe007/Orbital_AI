#!/usr/bin/env python3
"""Static smoke checks for the marketing page.

Catches the class of regressions that already bit production:
  - missing major sections
  - .reveal hidden until scroll (opacity: 0)
  - auto-loading the heavy /app embed iframe
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site" / "index.html"

REQUIRED_SECTION_IDS = ("stack", "platform", "how", "data", "oem")
REQUIRED_SNIPPETS = (
    "Launch the live demo",
    "Load live preview",
    "demo-load-btn",
    "cdn.tailwindcss.com",
)


def fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    if not SITE.is_file():
        fail(f"missing {SITE.relative_to(ROOT)}")

    html = SITE.read_text(encoding="utf-8")

    for tag in ("html", "head", "body", "section", "div"):
        opens = len(re.findall(rf"<{tag}[\s>]", html, flags=re.I))
        closes = len(re.findall(rf"</{tag}>", html, flags=re.I))
        if opens != closes:
            fail(f"<{tag}> imbalance: open={opens} close={closes}")

    section_count = len(re.findall(r"<section\b", html, flags=re.I))
    if section_count < 8:
        fail(f"expected ≥8 <section> tags, found {section_count}")

    for sid in REQUIRED_SECTION_IDS:
        if not re.search(rf'id=["\']{sid}["\']', html):
            fail(f"missing section id=#{sid}")

    for snippet in REQUIRED_SNIPPETS:
        if snippet not in html:
            fail(f"missing required snippet: {snippet!r}")

    # Hide-until-scroll was the "70% of site missing" bug.
    if re.search(r"\.reveal\s*\{[^}]*opacity\s*:\s*0", html):
        fail(".reveal must not default to opacity:0")

    # Auto-loading the dashboard iframe reintroduced browser hangs.
    if re.search(r'id=["\']demo-frame["\'][^>]*\ssrc=', html):
        fail("demo-frame must not have a static src (use click-to-load)")

    # Tailwind in <head> blocks first paint on this large page.
    head = html.split("</head>", 1)[0]
    if "cdn.tailwindcss.com" in head:
        fail("Tailwind CDN must not be in <head>; keep it at page bottom")

    print(
        f"OK: site smoke passed "
        f"({section_count} sections, ids={','.join(REQUIRED_SECTION_IDS)})"
    )


if __name__ == "__main__":
    main()
