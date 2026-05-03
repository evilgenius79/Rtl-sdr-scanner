#!/usr/bin/env bash
# Police scanner installer for Raspberry Pi 5 (Debian Bookworm).
# Idempotent: rerun safely. Each step checks before doing.
#
# Usage:  sudo ./scripts/install-pi.sh
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_USER="scanner"
DATA_DIR="/var/lib/police-scanner"
TR_BUILD_DIR="/opt/build/trunk-recorder"
LIBRTLSDR_BUILD_DIR="/opt/build/librtlsdr-blog"
MQTT_PLUGIN_BUILD_DIR="/opt/build/tr-plugin-mqtt"

log() { printf '\033[1;36m[install]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[fatal]\033[0m %s\n' "$*" >&2; exit 1; }

check_pi() {
  if ! grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null; then
    warn "Not detected as Raspberry Pi 5. Continuing anyway."
  fi
  if ! grep -qi "bookworm\|trixie" /etc/os-release; then
    warn "Expected Debian Bookworm or Trixie. Continuing anyway."
  fi
}

apt_install() {
  log "Installing apt dependencies..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq

  # GNU Radio's osmosdr binding is ABI-versioned in Debian (e.g.
  # libgnuradio-osmosdr0.2.0 on Bookworm, possibly different on Trixie).
  # Detect whatever version is actually packaged so this works on both.
  local gr_osmosdr_lib
  gr_osmosdr_lib=$(apt-cache search --names-only '^libgnuradio-osmosdr[0-9]' \
                   | awk '{print $1}' | sort -V | tail -n1)
  if [[ -z $gr_osmosdr_lib ]]; then
    warn "No libgnuradio-osmosdr* package found in apt; relying on gr-osmosdr deps to pull it in."
    gr_osmosdr_lib=""
  else
    log "Using $gr_osmosdr_lib"
  fi

  apt-get install -y --no-install-recommends \
    build-essential cmake git pkg-config \
    libusb-1.0-0-dev libssl-dev libcurl4-openssl-dev \
    liblog4cpp5-dev libpcap-dev libsndfile1-dev \
    gnuradio-dev gr-osmosdr ${gr_osmosdr_lib} \
    libuhd-dev libhackrf-dev \
    libboost-all-dev libgmp-dev liborc-0.4-dev \
    libcppunit-dev swig \
    fdkaac sox ffmpeg \
    mosquitto mosquitto-clients \
    python3 python3-venv python3-pip python3-dev \
    libsqlite3-dev \
    nginx certbot python3-certbot-nginx \
    ca-certificates curl jq \
    udev usbutils
}

blacklist_dvb() {
  local f=/etc/modprobe.d/blacklist-rtl.conf
  if [[ ! -f $f ]]; then
    log "Blacklisting DVB kernel modules so RTL-SDR can claim the device..."
    cat >"$f" <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
blacklist rtl8xxxu
EOF
    update-initramfs -u
  else
    log "DVB blacklist already in place."
  fi
}

build_librtlsdr_blog() {
  if [[ -x /usr/local/bin/rtl_test ]] && /usr/local/bin/rtl_test -t 2>&1 | grep -q "Found .* device(s)"; then
    log "librtlsdr already built and a dongle is present."
    # Continue anyway to ensure correct fork.
  fi
  log "Building librtlsdr-blog (RTL-SDR Blog v4 fork)..."
  mkdir -p "$(dirname "$LIBRTLSDR_BUILD_DIR")"
  if [[ ! -d $LIBRTLSDR_BUILD_DIR/.git ]]; then
    git clone --depth=1 https://github.com/rtlsdrblog/rtl-sdr-blog.git "$LIBRTLSDR_BUILD_DIR"
  else
    git -C "$LIBRTLSDR_BUILD_DIR" pull --ff-only
  fi
  rm -rf "$LIBRTLSDR_BUILD_DIR/build"
  mkdir -p "$LIBRTLSDR_BUILD_DIR/build"
  pushd "$LIBRTLSDR_BUILD_DIR/build" >/dev/null
  cmake .. -DINSTALL_UDEV_RULES=ON -DDETACH_KERNEL_DRIVER=ON -DCMAKE_BUILD_TYPE=Release
  make -j"$(nproc)"
  make install
  ldconfig
  popd >/dev/null
  install -Dm644 "$LIBRTLSDR_BUILD_DIR/rtl-sdr.rules" /etc/udev/rules.d/20-rtlsdr.rules
  udevadm control --reload-rules
  udevadm trigger
}

build_trunk_recorder() {
  if [[ -x /usr/local/bin/trunk-recorder ]]; then
    log "trunk-recorder already installed; pulling latest..."
  else
    log "Building trunk-recorder from source..."
  fi
  mkdir -p "$(dirname "$TR_BUILD_DIR")"
  if [[ ! -d $TR_BUILD_DIR/.git ]]; then
    git clone --depth=1 https://github.com/robotastic/trunk-recorder.git "$TR_BUILD_DIR"
  else
    git -C "$TR_BUILD_DIR" pull --ff-only
  fi
  rm -rf "$TR_BUILD_DIR/build"
  mkdir -p "$TR_BUILD_DIR/build"
  pushd "$TR_BUILD_DIR/build" >/dev/null
  cmake .. -DCMAKE_BUILD_TYPE=Release
  make -j"$(nproc)"
  make install
  popd >/dev/null
}

build_mqtt_plugin() {
  log "Building MQTT status plugin for trunk-recorder..."
  apt-get install -y --no-install-recommends libmosquitto-dev
  mkdir -p "$(dirname "$MQTT_PLUGIN_BUILD_DIR")"
  if [[ ! -d $MQTT_PLUGIN_BUILD_DIR/.git ]]; then
    git clone --depth=1 https://github.com/TrunkRecorder/trunk-recorder-mqtt-status.git "$MQTT_PLUGIN_BUILD_DIR"
  else
    git -C "$MQTT_PLUGIN_BUILD_DIR" pull --ff-only
  fi
  rm -rf "$MQTT_PLUGIN_BUILD_DIR/build"
  mkdir -p "$MQTT_PLUGIN_BUILD_DIR/build"
  pushd "$MQTT_PLUGIN_BUILD_DIR/build" >/dev/null
  cmake ..
  make -j"$(nproc)"
  make install
  popd >/dev/null
  ldconfig
}

create_user_and_dirs() {
  if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
    log "Creating system user '$SERVICE_USER'..."
    useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin --user-group "$SERVICE_USER"
    # Allow access to USB SDRs (group created by udev rules).
    if getent group plugdev >/dev/null; then
      usermod -aG plugdev "$SERVICE_USER"
    fi
  fi
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
    "$DATA_DIR" \
    "$DATA_DIR/recordings" \
    "$DATA_DIR/trunk-recorder" \
    "$DATA_DIR/trunk-recorder/captures" \
    "$DATA_DIR/trunk-recorder/talkgroups" \
    "$DATA_DIR/trunk-recorder/logs"
}

install_python_app() {
  log "Installing Python application into venv..."
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 /opt/police-scanner
  rsync -a --delete --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
    "$REPO_ROOT/" /opt/police-scanner/
  chown -R "$SERVICE_USER:$SERVICE_USER" /opt/police-scanner
  sudo -u "$SERVICE_USER" python3 -m venv /opt/police-scanner/.venv
  sudo -u "$SERVICE_USER" /opt/police-scanner/.venv/bin/pip install --upgrade pip wheel
  sudo -u "$SERVICE_USER" /opt/police-scanner/.venv/bin/pip install -e /opt/police-scanner
}

install_env_file() {
  if [[ ! -f /etc/police-scanner/env ]]; then
    log "Creating /etc/police-scanner/env (mode 0600). Edit before starting service."
    install -d -m 0750 -o root -g "$SERVICE_USER" /etc/police-scanner
    install -m 0640 -o root -g "$SERVICE_USER" "$REPO_ROOT/.env.example" /etc/police-scanner/env

    # Generate a strong random secret and bake it in so the service can start.
    local secret
    secret=$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')
    # Use awk for substitution to avoid shell-quoting pitfalls with secret characters.
    awk -v s="$secret" '
      /^SCANNER_SECRET_KEY=/ { print "SCANNER_SECRET_KEY=" s; next }
      { print }
    ' /etc/police-scanner/env > /etc/police-scanner/env.new
    mv /etc/police-scanner/env.new /etc/police-scanner/env
    chmod 0640 /etc/police-scanner/env
    chown root:"$SERVICE_USER" /etc/police-scanner/env
  else
    log "/etc/police-scanner/env already exists; leaving untouched."
  fi
}

install_uploadhook() {
  log "Installing uploadhook wrapper..."
  install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0755 "$DATA_DIR/trunk-recorder"
  install -m 0755 "$REPO_ROOT/scripts/uploadhook-wrapper.sh" \
    "$DATA_DIR/trunk-recorder/uploadhook"
}

install_systemd_units() {
  log "Installing systemd units..."
  install -m 0644 "$REPO_ROOT/systemd/police-scanner.service" /etc/systemd/system/police-scanner.service
  install -m 0644 "$REPO_ROOT/systemd/trunk-recorder.service" /etc/systemd/system/trunk-recorder.service
  systemctl daemon-reload
  systemctl enable mosquitto.service
  systemctl restart mosquitto.service
  log "Run: sudo systemctl start police-scanner.service"
  log "And: sudo systemctl start trunk-recorder.service  (after first-run setup writes its config)"
}

print_next_steps() {
  cat <<EOF

────────────────────────────────────────────────────────
Install complete. Next steps:

1. Flash unique serials to your 3 dongles:
     sudo ./scripts/flash-sdr-serials.sh

2. Edit /etc/police-scanner/env and fill in:
     RR_APP_KEY, RR_USERNAME, RR_PASSWORD,
     SCANNER_ADMIN_PASSWORD, SCANNER_PUBLIC_URL.

3. Start the service:
     sudo systemctl start police-scanner.service
     sudo systemctl status police-scanner.service

4. Browse to https://<pi-host>/ — log in with the admin
   password you set, then run the Setup wizard with ZIP 46173.

5. After the wizard saves a config, start the radio backend:
     sudo systemctl start trunk-recorder.service

For PUBLIC exposure, terminate TLS at nginx (certbot is installed):
     sudo certbot --nginx -d scanner.example.com
────────────────────────────────────────────────────────
EOF
}

main() {
  check_pi
  apt_install
  blacklist_dvb
  build_librtlsdr_blog
  build_trunk_recorder
  build_mqtt_plugin
  create_user_and_dirs
  install_python_app
  install_env_file
  install_uploadhook
  install_systemd_units
  print_next_steps
}

main "$@"
