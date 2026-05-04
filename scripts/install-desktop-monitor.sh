#!/usr/bin/env bash
# Installs a desktop autostart entry that opens a terminal at login showing
# the scanner's status, dongle enumeration, IP, and live service logs.
# Useful for headed Raspberry Pi installs where you want a glanceable health
# check without SSHing in.
#
# Run this AS YOUR LOGIN USER (not root, not via sudo) — autostart files live
# in your home directory. The script will prompt once for sudo to add you to
# the systemd-journal group (so the monitor's `journalctl -f` doesn't need
# sudo each time you log in).
set -Eeuo pipefail

if [[ $EUID -eq 0 ]]; then
  echo "Don't run this with sudo — autostart files live in your home dir." >&2
  echo "Run as your normal login user." >&2
  exit 1
fi

# ── Make `journalctl` work without sudo so the monitor doesn't pester you on
#    every login. The systemd-journal and adm groups grant journal-read access
#    on Debian-based systems. We also add 'adm' for belt-and-suspenders.
needs_logout=0
for grp in systemd-journal adm; do
  if ! id -nG "$USER" | tr ' ' '\n' | grep -qx "$grp"; then
    if getent group "$grp" >/dev/null; then
      echo "Adding $USER to '$grp' group (one-time sudo prompt)..."
      sudo usermod -aG "$grp" "$USER"
      needs_logout=1
    fi
  fi
done

# Pick a terminal we know is installed. Order = preference.
TERMINAL=""
for cand in lxterminal x-terminal-emulator gnome-terminal xterm xfce4-terminal mate-terminal; do
  if command -v "$cand" >/dev/null 2>&1; then
    TERMINAL="$cand"
    break
  fi
done
if [[ -z $TERMINAL ]]; then
  echo "No terminal emulator found (tried lxterminal, gnome-terminal, xterm, ...)." >&2
  echo "Install one with: sudo apt install lxterminal" >&2
  exit 1
fi

mkdir -p "$HOME/.config/autostart"
DESKTOP_FILE="$HOME/.config/autostart/scanner-monitor.desktop"

# The shell snippet that runs inside the terminal.
MONITOR_SH="$HOME/.local/bin/scanner-monitor.sh"
mkdir -p "$(dirname "$MONITOR_SH")"

cat > "$MONITOR_SH" <<'INNER'
#!/usr/bin/env bash
# Live status pane for the police scanner. Re-run anytime: ~/.local/bin/scanner-monitor.sh
clear
printf '\033[1;36m=== POLICE SCANNER MONITOR ===\033[0m\n\n'

printf '\033[1mServices\033[0m\n'
for svc in police-scanner.service trunk-recorder.service mosquitto.service; do
  state=$(systemctl is-active "$svc" 2>/dev/null || true)
  enabled=$(systemctl is-enabled "$svc" 2>/dev/null || true)
  case "$state" in
    active)   color='\033[32m' ;;
    inactive|failed) color='\033[31m' ;;
    *)        color='\033[33m' ;;
  esac
  printf "  %-30s ${color}%-10s\033[0m  (boot: %s)\n" "$svc" "$state" "$enabled"
done

printf '\n\033[1mNetwork\033[0m\n'
printf '  IP addresses : %s\n' "$(hostname -I)"
printf '  hostname     : %s\n' "$(hostname).local"
listen_line=$(ss -tlnp 2>/dev/null | awk '/:8080/{print $4; exit}' || echo unknown)
printf '  scanner port : %s\n' "$listen_line"

printf '\n\033[1mDongles (rtl_test)\033[0m\n'
if command -v rtl_test >/dev/null; then
  rtl_test -t 2>&1 | grep -E '^[[:space:]]*[0-9]+:|Serial number:|Found .* device' | sed 's/^/  /' || true
else
  printf '  rtl_test not in PATH\n'
fi

printf '\n\033[1mDisk / recordings\033[0m\n'
df -h /var/lib/police-scanner 2>/dev/null | tail -1 | awk '{printf "  free: %s of %s on %s\n", $4, $2, $6}' || true

printf '\n\033[1;36m=== Live logs (Ctrl-C to quit) ===\033[0m\n'
exec journalctl -u police-scanner.service -u trunk-recorder.service -f --output=short
INNER
chmod +x "$MONITOR_SH"

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Scanner Monitor
Comment=Live status + logs for the police scanner
Exec=$TERMINAL --title="Scanner Monitor" -e bash -c "$MONITOR_SH; exec bash"
Terminal=false
X-GNOME-Autostart-enabled=true
StartupNotify=false
EOF

cat <<MSG

Installed:
  $MONITOR_SH      (the monitor script — re-runnable anytime)
  $DESKTOP_FILE    (LXDE/GNOME autostart entry — runs at login)

Terminal selected: $TERMINAL

To test now without rebooting:
  $MONITOR_SH

To remove autostart later:
  rm $DESKTOP_FILE
MSG

if [[ $needs_logout -eq 1 ]]; then
  cat <<MSG

────────────────────────────────────────────────────────
You were added to the systemd-journal group, but Linux only picks up new
group memberships on a fresh login. To finish, do ONE of:
  • Log out of the desktop and log back in (recommended)
  • Reboot
  • Or, just for this terminal session: 'newgrp systemd-journal'
────────────────────────────────────────────────────────
MSG
fi
