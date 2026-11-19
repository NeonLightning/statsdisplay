#!/usr/bin/env bash
# Apply an aggressive display-appliance boot profile to Trixie Lite.
set -euo pipefail
[[ $(id -u) -eq 0 ]] || { echo 'Run with sudo' >&2; exit 1; }

BOOT=/boot/firmware/config.txt
cp -a "$BOOT" "$BOOT.before-statsdisplay-minimal" 2>/dev/null || true
python3 - "$BOOT" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
lines = p.read_text().splitlines()
out = []
skip = False
for line in lines:
    stripped = line.strip()
    if stripped == '# BEGIN appliance-minimal':
        skip = True
        continue
    if skip and stripped == '# END appliance-minimal':
        skip = False
        continue
    if skip:
        continue
    if stripped in {
        'dtparam=audio=on', 'dtparam=audio=off',
        'camera_auto_detect=1', 'camera_auto_detect=0',
        'display_auto_detect=1', 'display_auto_detect=0',
        'dtoverlay=disable-bt', 'dtoverlay=vc4-kms-v3d',
    }:
        continue
    if stripped.startswith('gpu_mem='):
        continue
    out.append(line)
out.extend([
    '', '# BEGIN appliance-minimal',
    'dtparam=audio=off',
    'dtoverlay=disable-bt',
    'camera_auto_detect=0',
    'display_auto_detect=0',
    'gpu_mem=16',
    'dtoverlay=vc4-kms-v3d',
    '# END appliance-minimal',
])
p.write_text('\n'.join(out) + '\n')
PY

# The appliance uses numeric LAN URLs. NetworkManager otherwise waits on an
# unavailable systemd-resolved plugin for roughly 40 seconds on this Pi Zero W.
mkdir -p /etc/NetworkManager/conf.d
cat > /etc/NetworkManager/conf.d/99-statsdisplay.conf <<'EOF'
[main]
plugins=keyfile
dns=none
rc-manager=unmanaged

[connectivity]
enabled=false

[device]
wifi.scan-rand-mac-address=no
EOF
chmod 0600 /etc/NetworkManager/conf.d/99-statsdisplay.conf

cat > /etc/modprobe.d/statsdisplay-appliance.conf <<'EOF'
# Display-appliance hardware: no audio, Bluetooth, camera, joystick, V4L2, or swap.
blacklist snd_bcm2835
blacklist snd_soc_core
blacklist snd_soc_hdmi_codec
blacklist bcm2835_v4l2
blacklist bcm2835_codec
blacklist bcm2835_isp
blacklist videodev
blacklist bluetooth
blacklist hci_uart
blacklist btbcm
blacklist joydev
blacklist raspberrypi_gpiomem
blacklist zram
EOF

# No serial console/login prompt is needed on a headless framebuffer appliance.
sed -i -E 's/[[:space:]]*console=serial0,[^ ]+//; s/^ //;' /boot/firmware/cmdline.txt 2>/dev/null || true
if ! grep -qE '(^| )console=tty1( |$)' /boot/firmware/cmdline.txt; then
    sed -i 's/^/console=tty1 /' /boot/firmware/cmdline.txt
fi

# HDMI remains useful as a network-status screen, not an interactive login console.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
if [[ -f $SCRIPT_DIR/pi_ip_console.sh && -f $SCRIPT_DIR/pi-ip-console.service ]]; then
    install -m 0755 "$SCRIPT_DIR/pi_ip_console.sh" /usr/local/sbin/statsdisplay-ip-console
    install -m 0644 "$SCRIPT_DIR/pi-ip-console.service" /etc/systemd/system/pi-ip-console.service
fi

mkdir -p /etc/systemd/system/stats-display.service.d
cat > /etc/systemd/system/stats-display.service.d/10-fast-start.conf <<'EOF'
[Unit]
After=
Wants=
EOF

# Mask static/generated work that is irrelevant on this fixed appliance.
units=(
  alsa-restore.service kmod-static-nodes.service systemd-binfmt.service
  proc-sys-fs-binfmt_misc.automount proc-sys-fs-binfmt_misc.mount
  modprobe@configfs.service modprobe@fuse.service
  rpi-resize-swap-file.service rpi-setup-loop@var-swap.service
  systemd-zram-setup@zram0.service dev-zram0.swap rpi-zram-writeback.timer
  serial-getty@ttyAMA0.service serial-getty@ttyS0.service
)
for unit in "${units[@]}"; do
    ln -sfn /dev/null "/etc/systemd/system/$unit"
done

# Disable rpi-swap generation; the 426 MiB appliance has enough headroom only
# while unnecessary Python/desktop components are absent.
install -d -m 0755 /etc/rpi/swap.conf.d
cat > /etc/rpi/swap.conf.d/90-statsdisplay.conf <<'EOF'
[Main]
Mechanism=none
EOF

# Persist generated Wi-Fi/Ethernet keyfiles before disabling netplan's boot generator.
# The generator was issuing four ~10-second daemon reloads on this Pi Zero W.
install -d -m 0700 /etc/NetworkManager/system-connections
if [[ -d /run/NetworkManager/system-connections ]]; then
    for profile in /run/NetworkManager/system-connections/*.nmconnection; do
        [[ -e $profile ]] || continue
        install -m 0600 "$profile" "/etc/NetworkManager/system-connections/$(basename "$profile")"
    done
fi
mkdir -p /etc/systemd/system-generators
ln -sfn /dev/null /etc/systemd/system-generators/netplan
ln -sfn /dev/null /etc/systemd/system-generators/rpi-swap-generator
ln -sfn /dev/null /etc/systemd/system-generators/zram-generator

systemctl daemon-reload
if [[ -x /usr/local/sbin/statsdisplay-ip-console ]]; then
    systemctl enable pi-ip-console.service
fi
echo 'Aggressive display-appliance profile applied. Reboot required.'
