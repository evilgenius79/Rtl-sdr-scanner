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
if ! command -v lsusb >/dev/null; then
  echo "lsusb not found. apt install usbutils" >&2
  exit 1
fi

# Wait up to N seconds for exactly one Realtek RTL28xx device to appear on USB.
# Returns 0 on success, 1 on timeout. The kernel can take 1-2 seconds to enumerate
# a freshly-plugged dongle, and rtl_eeprom races against that — so we poll lsusb.
wait_for_one_dongle() {
  local timeout_sec=${1:-8}
  local deadline=$(( $(date +%s) + timeout_sec ))
  while (( $(date +%s) < deadline )); do
    local count
    count=$(lsusb | grep -cE '0bda:(2832|2838|283[0-9a-f])' || true)
    if [[ $count -eq 1 ]]; then
      # Give librtlsdr a moment to claim the device away from any kernel module.
      sleep 1
      return 0
    fi
    sleep 1
  done
  return 1
}

current_serial() {
  # Print the serial of the (single) attached dongle, or empty on failure.
  rtl_eeprom 2>&1 | awk -F: '/Serial number:/ {gsub(/^[ \t]+|[ \t]+$/,"",$2); print $2; exit}'
}

prompt_flash() {
  local serial=$1 name=$2
  echo
  echo "──────────────────────────────────────────────"
  echo "Plug in ONLY the dongle that should become $name (serial $serial)."
  read -rp "Press Enter when ready, or Ctrl-C to abort: "

  if ! wait_for_one_dongle 8; then
    echo "Timed out waiting for exactly 1 RTL-SDR on USB. Currently detected:" >&2
    lsusb | grep -E '0bda:283' >&2 || echo "  (none)" >&2
    exit 1
  fi

  local before
  before=$(current_serial || true)
  echo "Detected dongle with current serial: ${before:-<unreadable>}"

  if [[ "$before" == "$serial" ]]; then
    echo "Dongle is already flashed to $serial — skipping."
    return 0
  fi

  echo "Writing $serial..."
  # rtl_eeprom -s prompts 'Write new configuration to device [y/n]?'.
  # Auto-confirm with 'y'.
  if ! printf 'y\n' | rtl_eeprom -s "$serial"; then
    echo "rtl_eeprom write failed — see output above." >&2
    exit 1
  fi

  echo
  echo "Done. UNPLUG this dongle now, wait 2 seconds, then plug it back in."
  read -rp "Press Enter when you've replugged: "

  if ! wait_for_one_dongle 8; then
    echo "Timed out waiting for the dongle to come back after replug." >&2
    exit 1
  fi

  local after
  after=$(current_serial || true)
  if [[ "$after" != "$serial" ]]; then
    echo "Serial verification FAILED. Expected $serial, got: ${after:-<unreadable>}" >&2
    rtl_eeprom 2>&1 | head -20 >&2
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
