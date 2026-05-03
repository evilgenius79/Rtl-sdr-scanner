#!/usr/bin/env bash
# Flash deterministic serial numbers to RTL-SDR dongles so trunk-recorder can
# select each by name.
#
# Convention used by this project:
#   00000101 → SDR-CTL    (control + low voice of the trunked site)
#   00000102 → SDR-VOICE  (high voice of the trunked site)
#   00000103 → SDR-CONV   (conventional VHF/UHF channels)
#
# Procedure: connect ONE dongle at a time, follow the prompts.
set -Eeuo pipefail

if ! command -v rtl_eeprom >/dev/null; then
  echo "rtl_eeprom not found. Run install-pi.sh first." >&2
  exit 1
fi

prompt_flash() {
  local serial=$1 name=$2
  echo
  echo "──────────────────────────────────────────────"
  echo "Plug in ONLY the dongle that should become $name (serial $serial)."
  read -rp "Press Enter when ready, or Ctrl-C to abort: "
  if ! rtl_eeprom 2>&1 | grep -q "Found 1 device"; then
    echo "Expected exactly 1 dongle attached. Got:" >&2
    rtl_eeprom 2>&1 | head -10 >&2
    exit 1
  fi
  rtl_eeprom -s "$serial"
  echo "Done. Unplug this dongle and replug to apply the new serial."
  read -rp "Press Enter when you've replugged: "
  if ! rtl_eeprom 2>&1 | grep -q "Serial number:.*$serial"; then
    echo "Serial verification FAILED — check rtl_eeprom output above." >&2
    exit 1
  fi
  echo "✓ $name now reports serial $serial."
}

prompt_flash 00000101 SDR-CTL
prompt_flash 00000102 SDR-VOICE
prompt_flash 00000103 SDR-CONV

echo
echo "All three dongles flashed. Plug all of them back in (powered hub recommended)."
echo "Verify with:  rtl_test -t   (should see 3 devices, serials 101/102/103)"
