# Police Scanner — Raspberry Pi 5 + 3× RTL-SDR

> **Built, Tested and Verified by Claude AI**

A self-hosted multi-SDR public-safety scanner built to beat a Uniden SDS100 on
usability. Type a ZIP code on first run and the importer pulls every relevant
trunked system, site, talkgroup, and conventional VHF/UHF channel straight from
[RadioReference.com](https://www.radioreference.com), plans the SDR allocation,
writes the [trunk-recorder](https://github.com/robotastic/trunk-recorder) config,
and starts decoding. The whole thing is driven from a custom Vue 3 web UI you
can pull up from any device on your LAN — or expose to the public internet
behind nginx + Let's Encrypt.

![Live view](docs/screenshots/live.png)

## Why build this when a Uniden SDS100 exists?

| Feature                                 | Uniden SDS100/SDS200 | This project           |
|-----------------------------------------|----------------------|------------------------|
| P25 Phase 1 + Phase 2                   | ✅                   | ✅ (via trunk-recorder) |
| Conventional FM, P25, DMR              | ✅                   | ✅                     |
| NXDN, ProVoice                          | ✅                   | ⚠️ DSDPlus / SDRTrunk sidecar (planned) |
| **Record every call, indefinitely**     | ❌ (small replay buf)| ✅                     |
| **Web UI accessible from any device**   | ❌ (tethered)        | ✅                     |
| **Multiple simultaneous listeners**     | ❌                   | ✅                     |
| **Full-text searchable archive**        | ❌                   | ✅                     |
| **Voice transcription (Whisper)**       | ❌                   | ✅ (optional)          |
| Hold / avoid / categorize talkgroups    | ✅                   | ✅                     |
| Hide encrypted talkgroups               | ✅                   | ✅                     |
| GPS-driven location-aware scanning      | ✅                   | ❌ (one site at a time)|
| Survives Pi reboot                      | n/a                  | ✅ (systemd units)     |
| Cost (~)                                | $700                 | ~$200                  |

The handheld will always win for "I want to listen in the car." This system
wins everywhere else.

## Screenshots

### Live view
Active call up top, last 25 calls below with one-click playback. Encrypted
talkgroups are dimmed and not auto-played. The top-right shows live WebSocket
connection status.

![Live](docs/screenshots/live.png)

### Recordings library
Search alpha tags, descriptions, or Whisper transcripts. Filter by
clear/encrypted. Inline HTML5 audio for every recording.

![Recordings](docs/screenshots/recordings.png)

### Talkgroups
Hold (force-listen), Avoid (skip), Hidden flags persist across re-imports of
the RadioReference data. Filter by category or free text.

![Talkgroups](docs/screenshots/talkgroups.png)

### Setup wizard
Type ZIP → preview systems and talkgroups → choose categories → save. The
wizard validates whether the chosen site fits your SDR count before writing
the trunk-recorder config.

![Setup](docs/screenshots/setup.png)

### Status
Real-time check on the radio backend, SDR enumeration (with per-dongle
serials), trunk-recorder service health, and disk free.

![Status](docs/screenshots/status.png)

### Login
Argon2id-backed login, server-side sessions, lockout after 5 failed attempts.

![Login](docs/screenshots/login.png)

## Architecture

```
┌─ Browser (LAN or WAN through nginx) ─────────────────────┐
│  Vue 3 (no build, vendored at /static/js/vendor/)        │
│  WebSocket for live events · HTML5 audio for playback   │
└──────────────────────────┬───────────────────────────────┘
                           │ HTTPS (nginx → :8080 loopback)
┌──────────────────────────▼───────────────────────────────┐
│  FastAPI / uvicorn — single Python process               │
│  • RadioReference SOAP client (zeep)                     │
│  • SDR planner + trunk-recorder config generator         │
│  • argon2id auth, server-side sessions, CSRF, rate-limit │
│  • SQLite (WAL) for users, talkgroups, calls, audit log  │
│  • WebSocket hub fed by MQTT subscriber                  │
│  • /api/tr/upload (loopback only) ingests audio per call │
└────────▲─────────────────────────┬───────────────────────┘
         │ MQTT (loopback Mosquitto) │ uploadScript hook
┌────────┴─────────────────────────┴───────────────────────┐
│  trunk-recorder (C++ / GNU Radio) — separate systemd unit │
│  Plugins: libmqtt_status_plugin.so + uploadhook wrapper   │
└──────────────────────────┬───────────────────────────────┘
                           │ librtlsdr-blog (built from source)
            ┌──────────────┼──────────────┐
            ▼              ▼              ▼
        SDR-CTL        SDR-VOICE      SDR-CONV
       (00000101)     (00000102)     (00000103)
                Powered USB 3 hub
                          │
                  Raspberry Pi 5
```

Two systemd units, totally decoupled:
- **`police-scanner.service`** — the FastAPI web app and ingest pipeline.
- **`trunk-recorder.service`** — the radio decoder. Restarts only when the
  config it reads (rendered by the wizard) actually exists.

## Hardware

| Item                           | Notes                                                                  |
|--------------------------------|------------------------------------------------------------------------|
| Raspberry Pi 5 (4 or 8 GB)     | 8 GB recommended if you'll enable Whisper. Active Cooler required.    |
| 3× **RTL-SDR Blog v4**         | TCXO + R828D. Flash unique serials with `scripts/flash-sdr-serials.sh`.|
| Powered USB 3 hub              | **Don't** power dongles from the Pi directly — voltage sag = drops.   |
| 700/800 MHz public-safety antenna | Tuned ¼-wave ground-plane outperforms a wideband discone.          |
| LMR-240 or LMR-400 coax        | RG-58 is OK only for runs ≤ 3 m.                                       |
| 64 GB+ microSD or USB SSD      | A24h × 32 kbps Opus = ~340 MB/day worst-case; sized for retention.    |
| Optional: NVMe M.2 (≥ 128 GB)  | Strongly preferred over SD. See below.                                |
| Optional: Hailo-8 AI Kit (~26 TOPS) | Used for accelerated Whisper transcription. See below.           |

### NVMe M.2 (recommended)

If you're booting from an NVMe HAT instead of SD, two settings to confirm
once before deploying:

1. **Boot order** must put NVMe first:
   ```bash
   sudo raspi-config   # → Advanced Options → Boot Order → NVMe/USB Boot
   ```
2. **PCIe Gen 3** for free perf (HAT permitting):
   ```bash
   echo "dtparam=pciex1_gen=3" | sudo tee -a /boot/firmware/config.txt
   sudo reboot
   ```

With ≥ 128 GB available, recordings storage stops being a constraint:
typical scanner duty cycles produce **3–4 GB/month** at 32 kbps M4A, so
you can comfortably set `RECORDING_RETENTION_DAYS=180` (or `0` for never)
in `/etc/police-scanner/env` and still have years of headroom. SQLite WAL
journaling also benefits significantly — random writes that would crawl
on SD run at full SSD speed. Filesystem: ext4 is the right call at this
size (no btrfs/ZFS overhead needed).

### Hailo-8 NPU (optional, for Whisper)

The Pi 5 AI Kit's Hailo-8 (~26 TOPS) is genuinely useful here, but only
for one specific thing: **Whisper transcription acceleration**. CPU-only
Whisper-tiny on the Pi 5 manages ~1–2× real-time; on Hailo it should run
~10–20× real-time, so a 30-second call gets transcribed in ~2 seconds and
becomes searchable in the recordings library essentially as it ends.

Integration is **not in v1** — left as a follow-up commit because the
Hailo SDK install is much easier to get right with the actual hardware in
front of you. When ready:

1. `sudo apt install hailo-all` (Hailo's apt repo provides the driver +
   `hailo-platform` Python bindings).
2. Pull a pre-converted `.hef` Whisper model from the
   [Hailo Model Zoo](https://github.com/hailo-ai/hailo_model_zoo).
3. Replace the stub `transcribe.py` worker with a Hailo inference loop
   (~200 lines) that watches the `Call` table for rows with
   `transcript_status='pending'`, transcribes the audio, and writes the
   text back. The DB column and env-var hook (`WHISPER_MODEL_PATH`) are
   already in place.

What the NPU **isn't** good for:

- **Trunked decoding (P25/DMR demod)** — that's complex-baseband DSP, not
  tensor math. The CPU-based GNU Radio chain in trunk-recorder is the
  right tool.
- **Encryption breaking** — AES is computationally hard, not a tensor
  problem. (Encrypted talkgroups stay flagged + hidden.)

Future-but-realistic NPU uses if you want them later: voice activity
detection to drop mic-keyup-no-speech calls, audio event classification
(gunshot/siren/keyword spotting) tied into push-notification rules.

## Quick install (Raspberry Pi OS Bookworm 64-bit)

```bash
git clone https://github.com/evilgenius79/Rtl-sdr-scanner.git
cd Rtl-sdr-scanner
sudo ./scripts/install-pi.sh
```

The installer (idempotent — safe to rerun):
1. Installs apt deps, GNU Radio, Mosquitto, nginx, certbot.
2. Builds **`librtlsdr-blog`** from source (mainline Osmocom is too old for v4).
3. Blacklists `dvb_usb_rtl28xxu` so the kernel doesn't fight us for the dongles.
4. Builds **trunk-recorder** + the **MQTT status plugin** from source.
5. Creates a `scanner` system user, `/var/lib/police-scanner/`, and the
   `uploadhook` wrapper.
6. Drops in two systemd units (sandboxed: `ProtectSystem=strict`, `NoNewPrivileges`,
   `SystemCallFilter`, etc.).
7. Writes `/etc/police-scanner/env` with a freshly-generated 64-byte secret.

Then, one-time SDR setup (do this once with each dongle plugged in alone):

```bash
sudo ./scripts/flash-sdr-serials.sh
```

This writes deterministic serials `00000101 / 00000102 / 00000103` so the
config can name them by role rather than USB port.

## First-run

1. Edit `/etc/police-scanner/env`:
   ```dotenv
   RR_APP_KEY=<from radioreference.com/account/api>
   RR_USERNAME=<your RR username>
   RR_PASSWORD=<your RR password>
   SCANNER_ADMIN_PASSWORD=<pick a strong one>
   SCANNER_PUBLIC_URL=https://scanner.example.com   # or http://raspberrypi.local:8080
   # For LAN-only first-run testing (NOT public exposure), also set:
   SCANNER_HOST=0.0.0.0
   ```
   `SCANNER_HOST` defaults to `127.0.0.1` (loopback only) so it's safe to
   leave alone if you're running nginx in front. For LAN-only testing
   without nginx, change to `0.0.0.0` so other devices on your network
   can reach the UI.
2. Start the web app: `sudo systemctl enable --now police-scanner.service`
3. Browse to the URL → log in as `admin` with the password you set.
4. Click **Setup**, type your ZIP code, look up.
5. Pick the trunked system + site, check the categories you care about, leave
   "Hide encrypted" on.
6. **Save** — the wizard writes `/var/lib/police-scanner/trunk-recorder/config.json`
   and the per-system talkgroup CSVs.
7. Start the radio backend: `sudo systemctl enable --now trunk-recorder.service`
8. Switch to **Live** — calls will start appearing.

## Public-facing deploy

The service is hardened for direct internet exposure. The recommended topology
is **nginx → loopback FastAPI**:

```bash
sudo cp nginx/police-scanner.conf /etc/nginx/sites-available/police-scanner
sudo ln -s ../sites-available/police-scanner /etc/nginx/sites-enabled/
sudo certbot --nginx -d scanner.example.com
```

In `/etc/police-scanner/env`:
```dotenv
SCANNER_HOST=127.0.0.1
SCANNER_PORT=8080
SCANNER_PUBLIC_URL=https://scanner.example.com
SCANNER_TRUST_PROXY=true
SCANNER_IP_ALLOWLIST=                          # optional: 192.168.0.0/16,1.2.3.4/32
```

What you get out of the box:
- TLS via Let's Encrypt; secure-cookie + HSTS toggle automatically.
- nginx blocks `/api/tr/*` so the trunk-recorder ingest can never be reached
  from the internet (also enforced in code as loopback-only + shared secret).
- argon2id passwords (`time_cost=3`, `memory_cost=64 MiB`).
- Server-side sessions; the cookie holds a random 32-byte token, the DB stores
  only its SHA-256.
- 5-failure / 15-minute lockout per username; sliding-window IP rate limits
  (10 logins/min, 120 API hits/min).
- Strict CSP (`script-src 'self' 'unsafe-eval'` only — the eval is unavoidable
  for runtime-template Vue without a build step), CSRF double-submit cookie,
  secure headers.
- systemd hardening: `ProtectSystem=strict`, `PrivateTmp`, `RestrictNamespaces`,
  `MemoryDenyWriteExecute`, `SystemCallFilter=@system-service`.

## SDR allocation logic

A single RTL-SDR captures ~2 MHz of usable spectrum at 2.4 Msps. Trunked-system
sites can span more than that — Project Hoosier SAFE-T's Knightstown cell, for
example, spans 4.86 MHz between its lowest and highest voice frequencies. The
planner uses a **greedy interval cover**: it walks the sorted frequency list
and opens a new SDR window whenever the next frequency is more than 2 MHz from
the current cluster's start. If more SDRs are needed than allocated, it fails
loud with an actionable message.

```
Frequencies:  769.81  770.50  772.00  773.00  774.68 MHz
              [───── SDR-CTL ─────]   [── SDR-VOICE ──]
              center 771.0 MHz       center 773.84 MHz
```

The third SDR (`SDR-CONV`) is reserved for conventional VHF/UHF. Its center is
chosen by an O(n) sliding-window over all the area's conventional frequencies,
maximizing the count covered. Anything outside that 2 MHz window is reported
back to the UI as "uncovered."

## Configuration reference

Every setting reads from `/etc/police-scanner/env` (which is `0640
root:scanner`). See [`.env.example`](./.env.example) for the complete list.
Highlights:

| Var                          | Purpose                                                     |
|------------------------------|-------------------------------------------------------------|
| `SCANNER_SECRET_KEY`         | Cookie/CSRF signing. ≥ 32 chars; auto-generated by installer.|
| `SCANNER_ADMIN_PASSWORD`     | Used only on first start to bootstrap the admin user.       |
| `SCANNER_TRUST_PROXY`        | Honor `X-Forwarded-For` (only behind nginx).                |
| `SCANNER_IP_ALLOWLIST`       | Comma CIDRs; empty = open.                                  |
| `RR_APP_KEY` / `_USERNAME` / `_PASSWORD` | RadioReference SOAP credentials.                |
| `RECORDING_RETENTION_DAYS`   | Auto-sweep older recordings. `0` = keep forever.            |
| `WHISPER_MODEL_PATH`         | Set to a `ggml-*.bin` to enable transcription.             |

## Operations

```bash
# Status
systemctl status police-scanner trunk-recorder
journalctl -u police-scanner -f
journalctl -u trunk-recorder -f

# Edit config from the web UI, then…
sudo systemctl restart trunk-recorder

# Disk usage
du -sh /var/lib/police-scanner/recordings/

# Audit log (every login attempt, lockout, setup change)
sqlite3 /var/lib/police-scanner/db.sqlite \
  'SELECT at, actor, ip, action, detail FROM auditevent ORDER BY at DESC LIMIT 50'
```

### Optional: desktop status monitor

If your Pi is set up with a desktop session, you can install a one-window
"glance pane" that opens at login showing service state, dongle enumeration,
the LAN IP, and live logs:

```bash
./scripts/install-desktop-monitor.sh    # run as your normal user, NOT root
```

That drops a `.desktop` autostart file in `~/.config/autostart/` and a
re-runnable status script at `~/.local/bin/scanner-monitor.sh`. Re-run
anytime to refresh the snapshot without rebooting.

## Troubleshooting

### "I can't reach the web UI from another device on my network"

Check `SCANNER_HOST` in `/etc/police-scanner/env`. The default is
`127.0.0.1` (loopback only), which is correct if you're using nginx in
front but means **only the Pi itself can reach the UI**. For LAN testing
without nginx, set `SCANNER_HOST=0.0.0.0` and restart:

```bash
sudo systemctl restart police-scanner.service
hostname -I              # shows the Pi's LAN IPs; browse to http://<that-ip>:8080
sudo ss -tlnp | grep 8080  # confirm uvicorn is actually listening on 8080
```

### "Dongles flash to 00000101 the first time but won't flash 00000102 the next"

There's a 1-2 second window after replug where the kernel hasn't
enumerated the device yet. The flash script handles this by polling
`lsusb`, but if you replug before the previous dongle has fully released,
the new one looks like the old one. Always:

1. Unplug the dongle.
2. Wait at least 2 seconds.
3. Plug the next dongle in firmly.
4. Wait for the script's "Detected dongle with current serial: …" line —
   if it shows `00000101` instead of the factory default, you've still
   got the old dongle plugged in. Type Ctrl-C, unplug, try again.

The factory default for RTL-SDR Blog v4 is usually `00000001`.

### "trunk-recorder runs but Control Channel Message Decode Rate stays 0/sec"

The decoder is tuned to a frequency where it can't hear (or there's no
P25 traffic to decode). Diagnose with a 30-second spectrum scan:

```bash
sudo systemctl stop trunk-recorder.service
rtl_power -d 0 -f 850M:860M:10k -g 40 -i 1 -e 30 /tmp/scan800.csv
rtl_power -d 0 -f 769M:776M:10k -g 40 -i 1 -e 30 /tmp/scan700.csv
echo "=== 850-860 MHz ==="
sort -t, -k7 -n -r /tmp/scan800.csv | awk -F, '{printf "%.4f MHz  %.1f dBm\n",$3/1e6,$7}' | head -10
echo "=== 769-776 MHz ==="
sort -t, -k7 -n -r /tmp/scan700.csv | awk -F, '{printf "%.4f MHz  %.1f dBm\n",$3/1e6,$7}' | head -10
```

What you'll see and what it means:

- **Strong narrow constant peak** at the configured control-channel
  frequency: site is active, decode failure is something else
  (PPM error, weak RSSI, modulation mismatch).
- **Strong narrow peak elsewhere in 850-855 MHz**: site moved to a
  different control channel — update `CONTROL_CH` in your config or
  re-run the Setup wizard.
- **Strong narrow peak in 769-775 MHz**: site migrated to 700 MHz.
  Indiana SAFE-T has been doing this for years. RR's free public
  listings can be stale; the wizard pulls live data when you have
  an API key.
- **No strong peaks anywhere**: antenna mismatch, site too far away,
  or you're scanning during a quiet period (control channels transmit
  continuously, so this is unusual — scan again at a busier time of day).

### "GNU Radio crashes with 'Permission denied [/home/scanner/.config/...]'"

Fixed in current install. If you're seeing it on an older deploy:

```bash
sudo systemctl stop police-scanner trunk-recorder
sudo usermod -d /var/lib/police-scanner scanner
sudo install -d -o scanner -g scanner -m 0750 \
    /var/lib/police-scanner/.config \
    /var/lib/police-scanner/.config/gnuradio \
    /var/lib/police-scanner/.cache
sudo systemctl start police-scanner trunk-recorder
```

The systemd unit also explicitly sets `HOME=/var/lib/police-scanner`,
so this only matters if your unit file is older than commit `b3bb3ef`.

### "Antenna picks up VHF fine but nothing on 700/800 MHz"

A wideband discone covers both bands. A small "rubber duck" whip
(what ships with most RTL-SDR kits) is tuned for ~150 MHz and is
significantly less sensitive at 700-850 MHz. Options:
1. Get a 700/800 MHz public-safety-band antenna (Nooelec, RTL-SDR Blog,
   and others sell them tuned for the band).
2. Use a discone or wideband scanner antenna outdoors with a low-loss
   coax run.
3. Even a wire cut to ~9 cm (¼-wave at 850 MHz) on a ground plane
   outperforms the stock whip on that band.

### "RadioReference site listings are stale"

Several Hoosier SAFE-T sites have migrated from 800 MHz to 700 MHz
over the last few years. RR's free public site listings can lag the
actual deployment by months. If `rtl_power` shows no signal at the
expected freq but a clear peak elsewhere, trust the spectrum, not RR.
Once your RR API key arrives, the Setup wizard pulls live system
data and reflects the current configuration.

### "I want to disable everything on boot"

```bash
sudo systemctl disable police-scanner.service trunk-recorder.service
```

To re-enable later:

```bash
sudo systemctl enable police-scanner.service trunk-recorder.service
```

## Tests

```bash
.venv/bin/python -m pytest server/tests/ -q
```

23 tests, ~1 s. Coverage:
- SDR planner edge cases (narrow site, wide site, dense site exceeding budget,
  far-apart pair fitting, conventional sliding window, atomic config write,
  CSV escaping)
- RadioReference response normalization
- Argon2id round-trip, token hashing, path-traversal guards, ZIP/serial regex
- **Live ASGI integration**: login, `/me`, wrong-password 401, unknown-user 401,
  lockout after 5 failures, protected-route 401, security-headers presence

## Project layout

```
Rtl-sdr-scanner/
├── scripts/
│   ├── install-pi.sh           # Idempotent Pi installer
│   ├── flash-sdr-serials.sh    # rtl_eeprom helper
│   ├── uploadhook-wrapper.sh   # trunk-recorder per-call hook
│   └── generate_screenshots.py # Reproducible README screenshot harness
├── systemd/                    # Sandboxed unit files
├── nginx/police-scanner.conf   # TLS reverse-proxy reference
├── docs/screenshots/           # README images
├── server/police_scanner/
│   ├── main.py                 # FastAPI app + lifespan
│   ├── settings.py             # Pydantic-validated env
│   ├── models.py               # SQLModel schema
│   ├── db.py                   # Async SQLite + WAL
│   ├── auth.py                 # argon2id + sessions
│   ├── security.py             # CSP/CSRF/IP allowlist middleware
│   ├── rate_limit.py           # Sliding-window per-IP buckets
│   ├── radioreference.py       # SOAP client (zeep)
│   ├── tr_config.py            # SDR planner + config generator
│   ├── mqtt_bridge.py          # paho → WebSocket
│   ├── ws_hub.py               # WebSocket fanout
│   ├── retention.py            # Recording retention sweep
│   ├── uploadhook.py           # POST audio to /api/tr/upload
│   ├── routes/                 # auth, setup, calls, talkgroups, status, tr_ingest
│   ├── templates/              # Jinja2 (login + SPA shell)
│   └── static/                 # Vue 3 (vendored), CSS, view components
└── server/tests/               # Unit + integration tests
```

## Glossary

| Term      | Meaning                                                                       |
|-----------|-------------------------------------------------------------------------------|
| **P25**   | APCO Project 25, the digital voice standard used by most US public safety.    |
| **Phase 1 / Phase 2** | FDMA 12.5 kHz vs TDMA 6.25 kHz channels.                          |
| **Trunked system** | Many talkgroups share a small pool of voice frequencies; a control channel tells radios where to listen. |
| **Control channel** | The frequency that broadcasts assignments. Scanner must follow it.   |
| **Talkgroup (TGID)** | Logical group; equivalent to a "channel" on conventional radio.     |
| **Simulcast** | Same content on multiple towers at once for wider coverage.              |
| **WACN / SysID / NAC** | P25 network identifiers; helps differentiate adjacent systems.    |
| **RFSS** | RF Sub-System; a subdivision of a P25 trunked system, contains sites.         |
| **Encrypted (E / DE)** | Audio scrambled with AES/DES; cannot be decoded without keys.    |
| **Conventional** | Each channel is a single frequency, not pooled. Most rural fire/EMS.      |

## Sources & credits

- [trunk-recorder](https://github.com/robotastic/trunk-recorder) — the radio backend
- [trunk-recorder MQTT plugin](https://github.com/TrunkRecorder/trunk-recorder-mqtt-status)
- [librtlsdr-blog](https://github.com/rtlsdrblog/rtl-sdr-blog) — RTL-SDR v4 driver fork
- [RadioReference Web Service v3.1](https://wiki.radioreference.com/index.php/RadioReference.com_Web_Service3.1)
- [Indiana Project Hoosier SAFE-T](https://www.radioreference.com/db/sid/8084) (test target)

## License

MIT.
