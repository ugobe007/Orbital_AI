#!/usr/bin/env python3
"""Orbital AI — OEM self-onboarding client.

A zero-dependency (stdlib only) script a robot company (OEM) runs to join Orbital AI and
choose which parts of their robot API to unlock for us. It talks to the public OEM
Onboarding API (``/api/oem/*``).

Typical flow:

    # 1) Register — prints your OEM id + API key (store the key; it's shown once).
    python3 scripts/oem_onboard.py register \
        --url https://orbital.onstage.bot \
        --company "Acme Robotics" --vendor "Unitree" \
        --email ops@acme.com --transport ros2

    # 2) See what your protocol can expose (the capability ceiling).
    python3 scripts/oem_onboard.py profile --url ... --oem-id oem-xxxx

    # 3) Unlock the scopes you're comfortable granting.
    python3 scripts/oem_onboard.py unlock --url ... --oem-id oem-xxxx \
        --api-key orb_xxx --scopes telemetry.read,control.velocity,control.estop

    # Revoke any time:
    python3 scripts/oem_onboard.py revoke --url ... --oem-id oem-xxxx \
        --api-key orb_xxx --scopes control.velocity

Scopes: telemetry.read, state.read, control.velocity, control.estop, control.teleop,
        mission.dispatch, camera.read, map.read
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


def _call(method: str, url: str, *, body: dict | None = None, api_key: str | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    if api_key:
        req.add_header("authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        print(f"HTTP {e.code}: {detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"Could not reach {url}: {e.reason}", file=sys.stderr)
        sys.exit(1)


def _base(url: str) -> str:
    return url.rstrip("/")


def cmd_register(args: argparse.Namespace) -> None:
    out = _call("POST", f"{_base(args.url)}/api/oem/register", body={
        "company_name": args.company, "vendor": args.vendor,
        "contact_email": args.email, "transport": args.transport, "website": args.website,
    })
    partner, cred = out["partner"], out["credential"]
    print("Registered with Orbital AI.\n")
    print(f"  OEM id:        {partner['id']}")
    print(f"  Vendor:        {partner['vendor']}  (transport: {partner['transport']})")
    print(f"  Status:        {partner['status']}")
    print(f"  Can unlock:    {', '.join(partner['ceiling_scopes']) or '(none)'}")
    print("\n  API KEY (store now — shown only once):")
    print(f"    {cred['api_key']}\n")
    print("Next: unlock scopes with")
    print(f"  python3 scripts/oem_onboard.py unlock --url {args.url} "
          f"--oem-id {partner['id']} --api-key {cred['api_key']} "
          f"--scopes telemetry.read,control.estop")


def cmd_unlock(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    out = _call("POST", f"{_base(args.url)}/api/oem/{args.oem_id}/scopes",
                body={"scopes": scopes}, api_key=args.api_key)
    print(f"Unlocked. Granted scopes: {', '.join(out['granted_scopes']) or '(none)'}")
    print(f"Status: {out['status']}")


def cmd_revoke(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    out = _call("DELETE", f"{_base(args.url)}/api/oem/{args.oem_id}/scopes",
                body={"scopes": scopes}, api_key=args.api_key)
    print(f"Revoked. Remaining scopes: {', '.join(out['granted_scopes']) or '(none)'}")
    print(f"Status: {out['status']}")


def cmd_profile(args: argparse.Namespace) -> None:
    out = _call("GET", f"{_base(args.url)}/api/oem/{args.oem_id}/profile")
    print(f"OEM {out['company_name']} ({out['vendor']}, {out['transport']}) — {out['status']}")
    print(f"  Ceiling:  {', '.join(out['ceiling_scopes']) or '(none)'}")
    print(f"  Granted:  {', '.join(out['granted_scopes']) or '(none)'}")
    print(f"  Missing:  {', '.join(out['missing_scopes']) or '(none)'}")
    print(f"  Monitor ready: {out['monitor_ready']}   Control ready: {out['control_ready']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Orbital AI OEM onboarding client")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("register", help="register your company + get an API key")
    r.add_argument("--url", required=True)
    r.add_argument("--company", required=True)
    r.add_argument("--vendor", required=True)
    r.add_argument("--email", required=True)
    r.add_argument("--transport", default="ros2", choices=["ros2", "grpc", "cloud_rest", "udp"])
    r.add_argument("--website", default=None)
    r.set_defaults(func=cmd_register)

    u = sub.add_parser("unlock", help="grant scopes (unlock parts of your API)")
    u.add_argument("--url", required=True)
    u.add_argument("--oem-id", required=True)
    u.add_argument("--api-key", required=True)
    u.add_argument("--scopes", required=True, help="comma-separated scope list")
    u.set_defaults(func=cmd_unlock)

    v = sub.add_parser("revoke", help="revoke scopes")
    v.add_argument("--url", required=True)
    v.add_argument("--oem-id", required=True)
    v.add_argument("--api-key", required=True)
    v.add_argument("--scopes", required=True)
    v.set_defaults(func=cmd_revoke)

    pr = sub.add_parser("profile", help="show integration readiness")
    pr.add_argument("--url", required=True)
    pr.add_argument("--oem-id", required=True)
    pr.set_defaults(func=cmd_profile)

    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
