"""trunk-recorder per-call hook.

trunk-recorder invokes the configured ``uploadScript`` with the path to the
just-completed audio file (and a JSON sidecar of metadata). We POST that to
our local API over loopback with a shared secret, which avoids dragging the
service code into trunk-recorder's runtime.

This file is the script trunk-recorder itself runs (a small executable). It
only depends on stdlib so it doesn't need our venv.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _post(url: str, secret: str, audio_path: Path, meta: dict) -> None:
    boundary = "----polscan" + str(int(time.time() * 1000))
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        b'Content-Disposition: form-data; name="meta"\r\nContent-Type: application/json\r\n\r\n'
    )
    body.extend(json.dumps(meta).encode())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\n".encode())
    body.extend(
        f'Content-Disposition: form-data; name="audio"; filename="{audio_path.name}"\r\n'
        f"Content-Type: audio/wav\r\n\r\n".encode()
    )
    body.extend(audio_path.read_bytes())
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    if not url.startswith(("http://127.0.0.1", "http://localhost", "https://127.0.0.1")):
        # Refuse non-loopback URLs so a misconfigured ingest URL can't exfiltrate audio.
        print(f"refusing non-loopback ingest URL: {url}", file=sys.stderr)
        sys.exit(5)
    req = urllib.request.Request(  # noqa: S310 — host validated above; loopback-only ingest
        url,
        data=bytes(body),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "X-Scanner-Secret": secret,
            "User-Agent": "trunk-recorder-uploadhook",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            resp.read()
    except urllib.error.HTTPError as exc:
        print(f"upload failed: {exc.code} {exc.read().decode(errors='replace')[:200]}", file=sys.stderr)
        sys.exit(1)
    except OSError as exc:
        print(f"upload connect failed: {exc}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("audio_file")
    p.add_argument("json_file")
    args = p.parse_args()

    audio = Path(args.audio_file)
    sidecar = Path(args.json_file)
    if not audio.exists() or not sidecar.exists():
        print("audio or json sidecar missing", file=sys.stderr)
        return 3

    try:
        meta = json.loads(sidecar.read_text("utf-8"))
    except json.JSONDecodeError:
        meta = {}

    url = os.environ.get("SCANNER_INGEST_URL", "http://127.0.0.1:8080/api/tr/upload")
    secret = os.environ.get("SCANNER_INGEST_SECRET", "")
    if not secret:
        print("SCANNER_INGEST_SECRET not set; refusing to upload", file=sys.stderr)
        return 4

    _post(url, secret, audio, meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
