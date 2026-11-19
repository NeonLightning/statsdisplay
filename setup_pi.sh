#!/usr/bin/env bash
# Configure Debian 13 (Trixie) Lite on Raspberry Pi Zero W / Zero 2 W.
set -euo pipefail

SERVER_URL=${STATS_SERVER_URL:-}
TOKEN=${STATS_TOKEN:-}
PI_USER=${SUDO_USER:-${PI_USER:-}}
INSTALL_DIR=/opt/statsdisplay
BOOT_FILE=/boot/firmware/config.txt
SPI_SPEED=${SPI_SPEED:-auto}
SKIP_PACKAGES=0
MINIMAL=0

usage() {
    cat <<'EOF'
Usage: sudo ./setup_pi.sh --server URL [options]

Options:
  --server URL       Stats endpoint (required, or STATS_SERVER_URL)
  --token TOKEN      Optional X-Stats-Token value
  --user USER        Runtime user (default: invoking sudo user)
  --spi-speed HZ     SPI clock for the piscreen overlay (default: 16000000)
  --skip-packages    Do not run apt
  --minimal           Also apply aggressive appliance minimization
  -h, --help         Show help
EOF
}

while (($#)); do
    case "$1" in
        --server) SERVER_URL=${2:?--server requires a URL}; shift 2 ;;
        --token) TOKEN=${2:?--token requires a value}; shift 2 ;;
        --user) PI_USER=${2:?--user requires a value}; shift 2 ;;
        --spi-speed) SPI_SPEED=${2:?--spi-speed requires a value}; shift 2 ;;
        --skip-packages) SKIP_PACKAGES=1; shift ;;
        --minimal) MINIMAL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Run as root: sudo ./setup_pi.sh --server http://HOST:8282/stats" >&2
    exit 1
fi

cd "$(dirname "$0")"
[[ -f display_client.py ]] || { echo "display_client.py is missing" >&2; exit 1; }
[[ -f $BOOT_FILE ]] || { echo "Expected $BOOT_FILE (Trixie/Raspberry Pi OS only)" >&2; exit 1; }
[[ -r /etc/os-release ]] || { echo "Cannot detect operating system" >&2; exit 1; }
. /etc/os-release
if [[ ${VERSION_CODENAME:-} != trixie ]]; then
    echo "Warning: this installer targets Debian 13 Trixie, detected '${PRETTY_NAME:-unknown}'." >&2
fi
if [[ -z $PI_USER || $PI_USER == root ]]; then
    PI_USER=$(getent passwd 1000 | cut -d: -f1 || true)
fi
id "$PI_USER" >/dev/null 2>&1 || { echo "Runtime user '$PI_USER' does not exist; use --user USER" >&2; exit 1; }
[[ $SERVER_URL =~ ^https?://[^[:space:]]+$ ]] || { echo "A valid http:// or https:// --server URL is required" >&2; exit 2; }
[[ $TOKEN != *[$'\n\r '] ]] || { echo "Token cannot contain whitespace" >&2; exit 2; }

# The original Zero W (BCM2835/ARMv6) needs a lower Piscreen clock than Zero 2 W.
if [[ $SPI_SPEED == auto ]]; then
    if grep -qi 'Raspberry Pi Zero W' /proc/device-tree/model 2>/dev/null; then
        SPI_SPEED=8000000
    else
        SPI_SPEED=16000000
    fi
fi
[[ $SPI_SPEED =~ ^[0-9]+$ ]] && ((SPI_SPEED >= 1000000 && SPI_SPEED <= 32000000)) || {
    echo "SPI speed must be auto or between 1000000 and 32000000" >&2; exit 2;
}

# Trixie Lite may not include pygame/numpy. Both are required by the direct framebuffer backend.
if ((SKIP_PACKAGES == 0)); then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        python3 python3-pygame python3-numpy fonts-dejavu-core
fi
python3 -c 'import pygame, numpy' >/dev/null

BOOT_CHANGED=0
BACKUP="$BOOT_FILE.statsdisplay.bak"
[[ -e $BACKUP ]] || cp -a "$BOOT_FILE" "$BACKUP"
CONFIG_BEFORE=$(sha256sum "$BOOT_FILE" | cut -d' ' -f1)
python3 - "$BOOT_FILE" "$SPI_SPEED" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
speed = sys.argv[2]
start = "# BEGIN statsdisplay"
end = "# END statsdisplay"
lines = path.read_text().splitlines()
out = []
in_block = False
for line in lines:
    if line == start:
        in_block = True
        continue
    if in_block and line == end:
        in_block = False
        continue
    if in_block:
        continue
    stripped = line.strip()
    # Replace legacy/manual equivalents to avoid duplicate overlays and conflicting speeds.
    if stripped == "dtparam=spi=on" or stripped == "#dtparam=spi=on":
        continue
    if re.fullmatch(r"dtoverlay=piscreen(?:,.*)?", stripped):
        continue
    out.append(line)
block = [
    "",
    start,
    "# 480x320 SPI framebuffer and touch controller used by stats-display",
    "dtparam=spi=on",
    f"dtoverlay=piscreen,speed={speed},drm",
    "max_framebuffers=2",
    end,
]
path.write_text("\n".join(out).rstrip() + "\n" + "\n".join(block) + "\n")
PY
CONFIG_AFTER=$(sha256sum "$BOOT_FILE" | cut -d' ' -f1)
[[ $CONFIG_BEFORE == "$CONFIG_AFTER" ]] || BOOT_CHANGED=1

# The application runs outside the invoking user's home so it also works at boot.
install -d -o "$PI_USER" -g "$PI_USER" -m 0755 "$INSTALL_DIR"
install -o "$PI_USER" -g "$PI_USER" -m 0755 display_client.py "$INSTALL_DIR/display_client.py"
install -o root -g root -m 0600 /dev/null /etc/statsdisplay.env
python3 - /etc/statsdisplay.env "$SERVER_URL" "$TOKEN" <<'PY'
from pathlib import Path
import sys

def quote(value):
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'

Path(sys.argv[1]).write_text(
    f'STATS_SERVER_URL={quote(sys.argv[2])}\nSTATS_TOKEN={quote(sys.argv[3])}\n'
)
PY
chmod 0600 /etc/statsdisplay.env

# Hardware access groups take effect after a new login/restart; a system service starts immediately.
usermod -aG video,input "$PI_USER"

# Migrate away from the repository's former per-user service before enabling the system service.
PI_UID=$(id -u "$PI_USER")
PI_RUNTIME=/run/user/$PI_UID
if [[ -d $PI_RUNTIME ]]; then
    runuser -u "$PI_USER" -- env XDG_RUNTIME_DIR="$PI_RUNTIME" \
        systemctl --user disable --now stats-display.service >/dev/null 2>&1 || true
fi
systemctl disable --now stats-display.service >/dev/null 2>&1 || true

cat > /etc/systemd/system/stats-display.service <<EOF
[Unit]
Description=Raspberry Pi computer statistics display
After=network.target
Wants=network.target

[Service]
Type=simple
User=$PI_USER
Group=$PI_USER
SupplementaryGroups=video input
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=/etc/statsdisplay.env
ExecStart=/usr/bin/python3 $INSTALL_DIR/display_client.py --display-driver framebuffer --fbdev /dev/fb1
Restart=on-failure
RestartSec=5
TimeoutStopSec=10
Nice=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable stats-display.service
if [[ -c /dev/fb1 ]]; then
    systemctl restart stats-display.service
    systemctl --no-pager --full status stats-display.service | sed -n '1,16p'
else
    echo "Configured, but /dev/fb1 will not exist until the Piscreen overlay loads."
fi

printf '\nTrixie Lite setup complete. Runtime user: %s\nServer: %s\n' "$PI_USER" "$SERVER_URL"
if ((MINIMAL)); then
    [[ -x ./minimize_pi.sh ]] || { echo "minimize_pi.sh is missing" >&2; exit 1; }
    ./minimize_pi.sh
fi
