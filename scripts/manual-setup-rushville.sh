#!/usr/bin/env bash
# Manual SAFE-T setup for Rushville/Henry County area.
#
# Use this to test the scanner BEFORE your RadioReference API key arrives.
# Drops a hand-crafted trunk-recorder config that decodes the Project Hoosier
# SAFE-T site closest to ZIP 46173 (Knightstown, Site 094). trunk-recorder
# will auto-discover voice channels from the control channel — calls will
# appear in the Live view as raw TGIDs (e.g. "TG 10101") with no friendly
# alpha tags until you re-run the Setup wizard with RR creds.
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

DATA_DIR="/var/lib/police-scanner"
TR_DIR="$DATA_DIR/trunk-recorder"
CONFIG="$TR_DIR/config.json"
TG_CSV="$TR_DIR/talkgroups/safet_knightstown.csv"

# SAFE-T Knightstown Site 094 control channel (verified via RadioReference DB)
CONTROL_CH=774681250         # 774.68125 MHz × 1e6

# Two SDR centers to cover the ~4.86 MHz site span at 2.4 Msps:
#   SDR 101: 771.0 MHz  → covers 770.0–772.0 MHz (low voice)
#   SDR 102: 773.5 MHz  → covers 772.5–774.5 MHz + control at 774.68 (control + high voice)
SDR_CTL_SERIAL="00000101"
SDR_VOICE_SERIAL="00000102"
SDR_CTL_CENTER=771000000
SDR_VOICE_CENTER=773500000

install -d -o scanner -g scanner -m 0750 "$TR_DIR" "$TR_DIR/talkgroups" "$TR_DIR/captures" "$TR_DIR/logs"

# Empty-ish talkgroups CSV (just the header). trunk-recorder will accept this
# and emit calls with TGID-only metadata until a real CSV replaces it.
cat > "$TG_CSV" <<'CSV'
Decimal,Hex,Mode,Alpha Tag,Description,Tag,Group,Priority
CSV
chown scanner:scanner "$TG_CSV"

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
      "center": $SDR_CTL_CENTER,
      "rate": 2400000,
      "ppm": 0,
      "gain": 36,
      "agc": false,
      "digitalRecorders": 4,
      "analogRecorders": 0,
      "driver": "osmosdr",
      "device": "rtl=$SDR_CTL_SERIAL"
    },
    {
      "center": $SDR_VOICE_CENTER,
      "rate": 2400000,
      "ppm": 0,
      "gain": 36,
      "agc": false,
      "digitalRecorders": 4,
      "analogRecorders": 0,
      "driver": "osmosdr",
      "device": "rtl=$SDR_VOICE_SERIAL"
    }
  ],
  "systems": [
    {
      "shortName": "safet_knightstown",
      "type": "p25",
      "modulation": "qpsk",
      "control_channels": [$CONTROL_CH],
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

cat <<EOF

────────────────────────────────────────────────────────
Manual SAFE-T config written to:
  $CONFIG
  $TG_CSV  (empty — calls will show as raw TGIDs)

Next:
  sudo systemctl restart trunk-recorder.service
  sudo journalctl -u trunk-recorder -f

In the trunk-recorder log you should see (within ~30 s):
  • "Decoding control channel"
  • "Trunked System: ... WACN: ..."
  • Call event lines as Sheriff/Fire/EMS keys up

Browse to the police-scanner web UI Live view — calls will appear as
"TG <number>" with no friendly names. When your RadioReference API key
arrives, run the Setup wizard with ZIP 46173 to overwrite this config
with the proper alpha tags.
────────────────────────────────────────────────────────
EOF
