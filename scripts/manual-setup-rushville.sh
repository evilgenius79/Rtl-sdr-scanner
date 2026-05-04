#!/usr/bin/env bash
# Manual scanner setup for Rushville, Rush County IN (ZIP 46173).
#
# Use this to test the scanner BEFORE your RadioReference API key arrives.
# Decodes the actual Rushville SAFE-T site (Site 076, 800 MHz, 1.48 MHz span)
# AND the local conventional VHF dispatch channels (Sheriff, Police, Fire/EMS).
#
# All frequencies sourced from RadioReference's free public site listings.
# When the RR API key arrives, run the Setup wizard with ZIP 46173 to overwrite
# this config with full talkgroup metadata (alpha tags, categories, units).
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

DATA_DIR="/var/lib/police-scanner"
TR_DIR="$DATA_DIR/trunk-recorder"
CONFIG="$TR_DIR/config.json"
TG_CSV="$TR_DIR/talkgroups/safet_rushville.csv"
CH_CSV="$TR_DIR/channels/conv_rush.csv"

# ── SAFE-T Rushville Site 076 (RFSS 1) — 800 MHz band ──────────────────────
# Control: 852.9625 MHz
# Voice:   851.4875, 851.9625, 852.4625, 852.9625
# Span:    851.4875 → 852.9625 = 1.48 MHz (fits in a single RTL-SDR window)
SAFET_CONTROL=852962500
SAFET_CENTER=852225000   # midpoint of 851.4875 and 852.9625

# ── Conventional VHF dispatch around Rushville ─────────────────────────────
# Cluster sits in 154.16–156.20 MHz, fits in one ~2 MHz SDR window centered ~155.18.
CONV_CENTER=155180000

# Dongle assignment:
#   SDR-CTL    (00000101) = SAFE-T site (single-window, both control + voice)
#   SDR-VOICE  (00000102) = idle (third dongle gets used for conventional)
#   SDR-CONV   (00000103) = Rush County conventional VHF
SAFET_SDR_SERIAL="00000101"
CONV_SDR_SERIAL="00000103"

install -d -o scanner -g scanner -m 0750 \
  "$TR_DIR" "$TR_DIR/talkgroups" "$TR_DIR/channels" "$TR_DIR/captures" "$TR_DIR/logs"

# Empty talkgroups CSV — calls render as raw TGIDs until the RR import runs.
cat > "$TG_CSV" <<'CSV'
Decimal,Hex,Mode,Alpha Tag,Description,Tag,Group,Priority
CSV
chown scanner:scanner "$TG_CSV"

# Conventional VHF channels (verified from RR free site listings + FCC ULS).
cat > "$CH_CSV" <<'CSV'
Frequency,Alpha,Description,Tone,Mode
155.625000,RushSO Disp,Rush Sheriff Dispatch,179.9 PL,FMN
158.820000,Rush LE,Sheriff & Rushville Police shared,,FMN
155.190000,Rushvl PD,Rushville Police Dispatch,131.8 PL,FMN
154.355000,Rush FD/EMS,Rush County Fire/EMS Dispatch,131.8 PL,FMN
154.160000,Rush FG,Rush County Fireground,,FMN
156.195000,Rushvl Fire,Rushville Fire/EMS Dispatch,131.8 PL,FMN
154.415000,Rushvl FG,Rushville Fireground,,FMN
154.265000,RushTwp FG1,Rushville Township Fireground 1,,FMN
159.360000,RushTwp FG2,Rushville Township Fireground 2,,FMN
CSV
chown scanner:scanner "$CH_CSV"

cat > "$CONFIG" <<JSON
{
  "ver": 2,
  "captureDir": "$TR_DIR/captures",
  "logFile": true,
  "logDir": "$TR_DIR/logs",
  "consoleLog": true,
  "logLevel": "info",
  "callTimeout": 3,
  "controlWarnRate": 5,
  "frequencyFormat": "mhz",
  "statusAsString": true,
  "instanceId": "police-scanner",
  "sources": [
    {
      "center": $SAFET_CENTER,
      "rate": 2400000,
      "ppm": 0,
      "gain": 36,
      "agc": false,
      "digitalRecorders": 4,
      "analogRecorders": 0,
      "driver": "osmosdr",
      "device": "rtl=$SAFET_SDR_SERIAL"
    },
    {
      "center": $CONV_CENTER,
      "rate": 2400000,
      "ppm": 0,
      "gain": 36,
      "agc": false,
      "digitalRecorders": 0,
      "analogRecorders": 4,
      "driver": "osmosdr",
      "device": "rtl=$CONV_SDR_SERIAL"
    }
  ],
  "systems": [
    {
      "shortName": "safet_rushville",
      "type": "p25",
      "modulation": "qpsk",
      "control_channels": [$SAFET_CONTROL],
      "talkgroupsFile": "$TG_CSV",
      "talkgroupDisplayFormat": "id",
      "hideEncrypted": true,
      "audioArchive": true,
      "transmissionArchive": false,
      "callLog": true,
      "compressWav": true,
      "compressBitrate": "32k",
      "uploadScript": "$TR_DIR/uploadhook",
      "minDuration": 0,
      "minTransmissionDuration": 0
    },
    {
      "shortName": "conv_rush",
      "type": "conventional",
      "channelFile": "$CH_CSV",
      "audioArchive": true,
      "callLog": true,
      "uploadScript": "$TR_DIR/uploadhook",
      "squelch": -55,
      "deemphasisTau": 0.000750
    }
  ],
  "plugins": [
    {
      "name": "MQTT Status",
      "library": "libmqtt_status_plugin.so",
      "broker": "tcp://127.0.0.1:1883",
      "topic": "trunk-recorder",
      "unit_topic": "trunk-recorder/units",
      "message_topic": "trunk-recorder/messages",
      "username": "",
      "password": "",
      "console_logs": false,
      "mqtt_audio": false,
      "mqtt_qos": 0
    }
  ]
}
JSON
chown scanner:scanner "$CONFIG"
chmod 0640 "$CONFIG"

cat <<'EOF'

────────────────────────────────────────────────────────
Manual config written for ZIP 46173 (Rushville, Rush County IN).

  Trunked: SAFE-T Site 076 (Rushville), 800 MHz
           Control 852.9625 MHz, span 1.48 MHz
           SDR 00000101 covers the whole site

  Conventional (SDR 00000103, centered 155.18 MHz):
    155.625  Rush Sheriff Disp
    155.190  Rushville Police Disp
    154.355  Rush Fire/EMS Disp
    156.195  Rushville Fire/EMS Disp
    154.160  Rush Fireground
    154.415  Rushville Fireground
    154.265  Rushville Township FG1
    159.360  Rushville Township FG2
    158.820  Sheriff/Rushville PD shared
    (158.820 falls outside the 2 MHz window; will be uncovered)

  SDR 00000102 sits idle — will get a job once you re-run the Setup
  wizard (it can become a second SAFE-T window for site redundancy, or
  cover school NXDN frequencies, etc.)

Next:
  sudo systemctl restart trunk-recorder.service
  sudo journalctl -u trunk-recorder -f

In the trunk-recorder log within ~30 s you should see:
  • "Decoding control channel 852962500"
  • "Trunked System: ... WACN: BEE00 SysID: 6BD"
  • Conventional channel monitors active on the VHF freqs
  • Call event lines as units key up

In the web UI Live view, trunked calls appear as "TG <number>"; conventional
calls show their alpha tag (RushSO Disp, etc.) since those came from the CSV
above. When your RR API key arrives, the Setup wizard will fill in talkgroup
alpha tags for the trunked system too.
────────────────────────────────────────────────────────
EOF
