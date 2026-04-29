# Police Scanner — Pi 5 + 3× RTL-SDR

A multi-SDR police scanner that aims to beat a Uniden SDS100 on usability:

- **Type a ZIP code** → it pulls systems, sites, talkgroups, and conventional
  channels straight from RadioReference.com.
- **Auto-allocates 3 RTL-SDRs** to cover one trunked site (even when the site
  span exceeds one dongle's bandwidth) plus conventional VHF/UHF.
- **Custom Vue 3 web UI** showing live calls, recent activity, recording
  library with search, talkgroup grid (hold/avoid/hide), and system status.
- **Public-facing-ready**: argon2 password hashing, server-side sessions,
  CSRF, secure headers, IP allowlist, rate limiting, systemd sandboxing,
  TLS via nginx + Let's Encrypt.

## Hardware

| Item                       | Notes                                                           |
|----------------------------|-----------------------------------------------------------------|
| Raspberry Pi 5 (4 or 8 GB) | Active Cooler required.                                         |
| 3× RTL-SDR Blog v4         | Flash unique serials with `scripts/flash-sdr-serials.sh`.       |
| Powered USB 3 hub          | Don't power dongles from the Pi directly.                       |
| 700/800 MHz antenna        | Tuned ¼-wave outperforms a wideband discone for public-safety.  |
| Decent coax (LMR-240+)     | Avoid RG-58 over 3 m.                                           |

## Install

On a fresh Raspberry Pi OS Bookworm (64-bit):

```bash
git clone https://github.com/<you>/Rtl-sdr-scanner.git
cd Rtl-sdr-scanner
sudo ./scripts/install-pi.sh
```

The installer:
- builds **librtlsdr-blog** from source (mainline Osmocom is too old for v4);
- blacklists the kernel DVB driver (`dvb_usb_rtl28xxu`);
- builds **trunk-recorder** + the **MQTT status plugin**;
- creates a `scanner` system user and `/var/lib/police-scanner/`;
- installs systemd units and a Mosquitto broker bound to loopback;
- writes `/etc/police-scanner/env` (mode 0640, owned by root:scanner) with a
  freshly-generated `SCANNER_SECRET_KEY`.

Then:

```bash
# 1. Flash deterministic serials onto each dongle (one at a time):
sudo ./scripts/flash-sdr-serials.sh

# 2. Edit /etc/police-scanner/env — fill in:
#      RR_APP_KEY, RR_USERNAME, RR_PASSWORD,
#      SCANNER_ADMIN_PASSWORD, SCANNER_PUBLIC_URL.
sudo nano /etc/police-scanner/env

# 3. Start the web service:
sudo systemctl start police-scanner.service
sudo systemctl enable police-scanner.service

# 4. Browse to https://<pi>/  → log in → run the Setup wizard with your ZIP.
#    The wizard writes /var/lib/police-scanner/trunk-recorder/config.json.

# 5. Start the radio backend:
sudo systemctl start trunk-recorder.service
sudo systemctl enable trunk-recorder.service
```

## Public-facing deployment

This service is designed to live behind nginx + Let's Encrypt.

1. Point a DNS record at the Pi (or use a tunnel like Cloudflare).
2. `sudo certbot --nginx -d scanner.example.com`.
3. `nginx/police-scanner.conf` is included as a reference site.
4. In `/etc/police-scanner/env`, set:
   - `SCANNER_HOST=127.0.0.1` (local-only listener; nginx proxies to it)
   - `SCANNER_PUBLIC_URL=https://scanner.example.com`
   - `SCANNER_TRUST_PROXY=true`
   - `SCANNER_IP_ALLOWLIST=` (optional CIDRs to restrict access).
5. Restart: `sudo systemctl restart police-scanner.service`.

## RadioReference credentials

You need three things:

- A Premium subscription (~$30/yr).
- Your username + password.
- An **appKey** — request one at <https://www.radioreference.com/account/api>.

Place all three in `/etc/police-scanner/env`. They never leave the Pi (the
Vue UI never receives the password — only a "configured: yes/no" indicator
in the status panel).

## What's deliberately not in v1

- Whisper.cpp transcription is scaffolded only (set `WHISPER_MODEL_PATH`
  to enable when you've downloaded a `ggml-*.bin` model).
- DMR Tier III / NXDN trunked decoding (trunk-recorder gap). Add an SDRTrunk
  sidecar with a 4th dongle if needed; its uploadScript can hit
  `/api/tr/upload` with the same shared-secret contract.
- Mobile push notifications.

## Project layout

```
Rtl-sdr-scanner/
├── scripts/                 # install-pi.sh, flash-sdr-serials.sh
├── systemd/                 # service units (sandboxed)
├── nginx/                   # TLS reverse-proxy reference config
├── trunk_recorder/          # config template (rendered at runtime)
├── server/police_scanner/
│   ├── settings.py          # env-driven, validated
│   ├── models.py            # SQLModel schema
│   ├── db.py                # async SQLite, WAL mode
│   ├── radioreference.py    # SOAP client (zeep)
│   ├── tr_config.py         # trunk-recorder config generator
│   ├── auth.py              # argon2 + sessions
│   ├── security.py          # CSP/CSRF/IP allowlist
│   ├── rate_limit.py
│   ├── mqtt_bridge.py       # paho → WS
│   ├── ws_hub.py
│   ├── routes/              # auth, setup, calls, talkgroups, tr_ingest, status
│   ├── templates/           # Jinja2 (login + SPA shell)
│   └── static/              # Vue 3 (no build step)
└── server/tests/            # importer + config-generator unit tests
```

## License

MIT.
