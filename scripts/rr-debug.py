#!/usr/bin/env python3
"""Dump raw RadioReference responses for a ZIP code.

Usage (on the Pi, with the project venv active or via the venv's python):

    sudo -u scanner /opt/police-scanner/.venv/bin/python \
        /home/scanner/Rtl-sdr-scanner/scripts/rr-debug.py 46173

Prints whatever getZipcodeInfo / getCountyInfo / getStateInfo return, so we
can see why ZIP 46173 finds no trunked systems even though Indiana SAFE-T
covers Rush County. Reads RR_APP_KEY / RR_USERNAME / RR_PASSWORD from the
environment (or from /etc/police-scanner/scanner.env if present and run as
root) so it picks up exactly what the running service uses.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from pprint import pformat


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit() or len(sys.argv[1]) != 5:
        print("usage: rr-debug.py <5-digit ZIP>", file=sys.stderr)
        return 2

    for candidate in (
        Path("/etc/police-scanner/scanner.env"),
        Path.home() / "Rtl-sdr-scanner" / ".env",
    ):
        _load_env_file(candidate)

    app_key = os.environ.get("RR_APP_KEY", "").strip()
    username = os.environ.get("RR_USERNAME", "").strip()
    password = os.environ.get("RR_PASSWORD", "").strip()
    if not (app_key and username and password):
        print("RR_APP_KEY / RR_USERNAME / RR_PASSWORD not set", file=sys.stderr)
        return 3

    try:
        import requests
        from zeep import Client
        from zeep.helpers import serialize_object
        from zeep.transports import Transport
    except ImportError as exc:
        print(f"missing dependency: {exc}", file=sys.stderr)
        print("run with the project venv's python", file=sys.stderr)
        return 4

    public = os.environ.get("SCANNER_PUBLIC_URL", "https://rushcounty.online").rstrip("/")
    session = requests.Session()
    session.headers.update({"Origin": public, "Referer": public + "/"})
    transport = Transport(session=session)
    client = Client(
        "https://api.radioreference.com/soap2/?wsdl&v=latest",
        transport=transport,
    )
    auth = {
        "appKey": app_key,
        "username": username,
        "password": password,
        "version": "latest",
        "style": "rpc",
    }

    zip_code = sys.argv[1]
    print(f"=== getZipcodeInfo({zip_code}) ===")
    z = serialize_object(client.service.getZipcodeInfo(zip_code, auth)) or {}
    print(pformat(z, width=120))

    ctid = z.get("ctid") or z.get("countyId")
    stid = z.get("stid") or z.get("stateId")
    if not ctid:
        print("no county id returned — bailing")
        return 5

    print(f"\n=== getCountyInfo({ctid}) ===")
    c = serialize_object(client.service.getCountyInfo(int(ctid), auth)) or {}
    # Trim to keys + first item of each list so the output stays readable.
    summary = {k: (v if not isinstance(v, list) else f"[{len(v)} items] e.g. {v[0] if v else None}") for k, v in c.items()}
    print(pformat(summary, width=120))
    print("\n--- top-level keys ---")
    print(sorted(c.keys()))

    if stid:
        print(f"\n=== getStateInfo({stid}) ===")
        try:
            s = serialize_object(client.service.getStateInfo(int(stid), auth)) or {}
            summary = {k: (v if not isinstance(v, list) else f"[{len(v)} items] e.g. {v[0] if v else None}") for k, v in s.items()}
            print(pformat(summary, width=120))
            print("\n--- top-level keys ---")
            print(sorted(s.keys()))
        except Exception as exc:
            print(f"getStateInfo failed: {exc}")
    else:
        print("\nno state id in zipcode info; skipping getStateInfo")

    return 0


if __name__ == "__main__":
    sys.exit(main())
