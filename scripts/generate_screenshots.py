"""Generate README screenshots from a running dev instance with mock data.

Usage:  .venv/bin/python scripts/generate_screenshots.py

Spins up uvicorn against a throw-away SQLite DB seeded with realistic talkgroups
and calls (modeled on Project Hoosier SAFE-T / Rush County), uses Playwright to
log in and navigate each view, and writes PNGs to docs/screenshots/.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _seed(env_dir: Path) -> None:
    os.environ["SCANNER_SECRET_KEY"] = secrets.token_urlsafe(64)
    os.environ["SCANNER_PUBLIC_URL"] = "http://127.0.0.1"
    os.environ["SCANNER_DATA_DIR"] = str(env_dir)
    os.environ["SCANNER_RECORDINGS_DIR"] = str(env_dir / "recordings")
    os.environ["SCANNER_DB_PATH"] = str(env_dir / "db.sqlite")
    os.environ["SCANNER_TR_CONFIG_PATH"] = str(env_dir / "trunk-recorder/config.json")
    os.environ["SCANNER_TR_CAPTURE_DIR"] = str(env_dir / "trunk-recorder/captures")
    os.environ["MQTT_TOPIC_BASE"] = "tr-demo"
    from police_scanner.db import init_db, session
    from police_scanner.models import (
        Call,
        ConventionalChannel,
        Site,
        System,
        SystemType,
        Talkgroup,
        utcnow,
    )

    await init_db()
    async with session() as s:
        sys_row = System(
            rr_sid=8084,
            short_name="hoosier_safe_t_8084",
            description="Indiana Project Hoosier SAFE-T",
            type=SystemType.P25,
            wacn="BEE00",
            sysid="6BD",
        )
        s.add(sys_row)
        await s.flush()
        s.add(
            Site(
                system_id=sys_row.id,
                name="Knightstown (Site 094)",
                rfss=1,
                site_number=94,
                control_channels="774681250",
                voice_channels="769818750,770506250,772006250,773000000,774681250",
                span_hz=4_862_500,
            )
        )
        conv_sys = System(short_name="conv", description="Rush County conventional", type=SystemType.CONVENTIONAL)
        s.add(conv_sys)
        await s.flush()

        talkgroups_seed = [
            (10101, "RushSO Disp", "Sheriff Dispatch", "Law Enforcement", "T", False),
            (10102, "RushSO Tac1", "Sheriff Tactical", "Law Enforcement", "T", False),
            (10103, "Rushvl PD", "Rushville Police", "Law Enforcement", "T", False),
            (10104, "Rush FD", "Rush Fire/EMS Dispatch", "Fire/EMS", "T", False),
            (10105, "Rush EMS", "Rush County EMS", "Fire/EMS", "T", False),
            (10106, "Rush Hwy", "Highway Department", "Public Works", "D", False),
            (10301, "ISP Disp 51", "ISP District 51 Pendleton", "ISP", "T", False),
            (10738, "ISP GovSec", "ISP Government Security", "ISP", "TE", True),
            (10401, "Mut Aid Tac", "Statewide Mutual Aid", "Mutual Aid", "T", False),
            (10402, "Schools Comm", "School Bus Common", "Schools", "D", False),
        ]
        for tgid, alpha, descr, cat, mode, enc in talkgroups_seed:
            s.add(
                Talkgroup(
                    system_id=sys_row.id,
                    tgid=tgid,
                    alpha=alpha,
                    description=descr,
                    category=cat,
                    tag=cat,
                    mode=mode,
                    encrypted=enc,
                    hidden=enc,
                )
            )

        for f, alpha, descr, mode, tone, lic in [
            (155_625_000, "RushSO Disp", "Rush Sheriff: Dispatch", "FMN", "179.9 PL", "WZX813"),
            (154_355_000, "Rush FD Disp", "Rush Fire/EMS: Dispatch", "FMN", "131.8 PL", "KVC540"),
            (156_195_000, "Rushvl Fire", "Rushville Fire/EMS Dispatch", "FMN", "131.8 PL", "WQCB683"),
            (155_190_000, "Rushvl PD", "Rushville Police Dispatch", "FMN", "131.8 PL", "WZX813"),
            (154_415_000, "Rushvl Fgrd", "Rushville Fireground", "FMN", "", "KJD846"),
            (159_360_000, "Rushvl Twp 2", "Rushville Township Fireground 2", "FMN", "", "WQGI878"),
        ]:
            s.add(
                ConventionalChannel(
                    system_id=conv_sys.id,
                    frequency_hz=f,
                    alpha=alpha,
                    description=descr,
                    mode=mode,
                    tone=tone,
                    license=lic,
                )
            )

        now = utcnow()
        recent_calls = [
            (10101, "RushSO Disp", "Sheriff Dispatch", "Law Enforcement", False, 155_625_000, 4.2, 25),
            (10103, "Rushvl PD", "Rushville Police", "Law Enforcement", False, 155_190_000, 11.8, 90),
            (10105, "Rush EMS", "Rush County EMS", "Fire/EMS", False, 0, 6.1, 180),
            (10401, "Mut Aid Tac", "Statewide Mutual Aid", "Mutual Aid", False, 0, 2.4, 240),
            (10738, "ISP GovSec", "ISP Government Security", "ISP", True, 0, 9.0, 320),
            (10101, "RushSO Disp", "Sheriff Dispatch", "Law Enforcement", False, 155_625_000, 7.5, 410),
            (10104, "Rush FD", "Rush Fire/EMS Dispatch", "Fire/EMS", False, 154_355_000, 14.2, 600),
            (10301, "ISP Disp 51", "ISP District 51 Pendleton", "ISP", False, 0, 3.3, 720),
            (10106, "Rush Hwy", "Highway Department", "Public Works", False, 0, 1.8, 1100),
            (10102, "RushSO Tac1", "Sheriff Tactical", "Law Enforcement", False, 0, 22.4, 1500),
        ]
        for i, (tgid, alpha, desc, cat, enc, freq, dur, age) in enumerate(recent_calls):
            start = now - timedelta(seconds=age)
            s.add(
                Call(
                    system_short_name="hoosier_safe_t_8084" if tgid >= 10300 or tgid < 10200 else "hoosier_safe_t_8084",
                    call_id=str(50000 + i),
                    talkgroup_tgid=tgid,
                    talkgroup_alpha=alpha,
                    talkgroup_description=desc,
                    talkgroup_category=cat,
                    encrypted=enc,
                    frequency_hz=freq,
                    start_time=start,
                    end_time=start + timedelta(seconds=dur),
                    duration_seconds=dur,
                    audio_path="" if enc else f"hoosier_safe_t_8084/2026/04/29/{start.strftime('%H%M%S')}_{50000+i}.m4a",
                    audio_size_bytes=int(dur * 4000) if not enc else 0,
                    audio_duration_seconds=dur,
                )
            )


def _start_server(env_dir: Path, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env.update(
        {
            "SCANNER_HOST": "127.0.0.1",
            "SCANNER_PORT": str(port),
            "SCANNER_PUBLIC_URL": f"http://127.0.0.1:{port}",
            "SCANNER_SECRET_KEY": secrets.token_urlsafe(64),
            "SCANNER_ADMIN_USERNAME": "demo",
            "SCANNER_ADMIN_PASSWORD": "demo-password-for-screenshots",
            "SCANNER_DATA_DIR": str(env_dir),
            "SCANNER_RECORDINGS_DIR": str(env_dir / "recordings"),
            "SCANNER_DB_PATH": str(env_dir / "db.sqlite"),
            "SCANNER_TR_CONFIG_PATH": str(env_dir / "trunk-recorder/config.json"),
            "SCANNER_TR_CAPTURE_DIR": str(env_dir / "trunk-recorder/captures"),
            "MQTT_HOST": "127.0.0.1",
            "MQTT_TOPIC_BASE": "tr-demo",
            "PYTHONPATH": str(ROOT / "server"),
        }
    )
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "police_scanner.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


def _wait_ready(port: int, proc: subprocess.Popen, timeout: float = 30.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
            raise RuntimeError(f"server exited early:\n{err}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=1) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    err = ""
    if proc.stderr:
        proc.stderr.close()
    raise RuntimeError(f"server did not become ready within {timeout}s")


async def _shoot(port: int, out_dir: Path) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        context = await browser.new_context(viewport={"width": 1400, "height": 900})

        # Surface browser console + failed network for diagnosis.
        def on_console(msg):
            if msg.type in ("error", "warning"):
                print(f"  console.{msg.type}: {msg.text}")
        def on_pageerror(exc):
            print(f"  pageerror: {exc}")
        def on_requestfailed(req):
            print(f"  requestfailed: {req.url} -> {req.failure}")

        # ── Login screenshot (no auth needed)
        page = await context.new_page()
        page.on("console", on_console)
        page.on("pageerror", on_pageerror)
        page.on("requestfailed", on_requestfailed)
        await page.goto(f"http://127.0.0.1:{port}/login")
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path=str(out_dir / "login.png"), full_page=False)
        print("✓ login.png")

        # ── Login via API (form submit + nav timing has been flaky in this harness)
        api_resp = await context.request.post(
            f"http://127.0.0.1:{port}/api/auth/login",
            form={"username": "demo", "password": "demo-password-for-screenshots"},
        )
        if not api_resp.ok:
            body = await api_resp.text()
            raise RuntimeError(f"login POST failed {api_resp.status}: {body[:300]}")
        await page.goto(f"http://127.0.0.1:{port}/")
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(800)

        for view, fname in [
            ("live", "live.png"),
            ("recordings", "recordings.png"),
            ("talkgroups", "talkgroups.png"),
            ("setup", "setup.png"),
            ("status", "status.png"),
        ]:
            await page.evaluate(f"location.hash = '{view}'")
            await page.wait_for_timeout(900)
            await page.screenshot(path=str(out_dir / fname), full_page=False)
            print(f"✓ {fname}")

        await browser.close()


async def main() -> None:
    out_dir = ROOT / "docs" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="scanner-shots-") as tmp:
        env_dir = Path(tmp)
        port = _free_port()
        # Seed before launching server so init_db is idempotent.
        await _seed(env_dir)
        proc = _start_server(env_dir, port)
        try:
            _wait_ready(port, proc)
            await _shoot(port, out_dir)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
