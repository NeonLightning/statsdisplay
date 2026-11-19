# Raspberry Pi Computer Stats Display

A LAN computer-monitoring dashboard for a Raspberry Pi with a 480×320 SPI framebuffer display and XPT2046/ADS7846 touchscreen.

## Features

- CPU, memory, disk, uptime, and network overview
- Top three processes by CPU, memory, and disk I/O on one page
- Direct `/dev/fb1` SPI framebuffer output at 15 FPS
- Touch page switching through Linux input events
- Cyberpunk color theme and monospace typography
- Connection state and recovery display
- User-level systemd services for automatic startup

## Host computer

Install dependencies and start the stats server:

```bash
python3 -m pip install -r requirements.txt
python3 stats_server.py --host 0.0.0.0 --port 8282
```

Allow TCP port 8282 through the computer firewall only for trusted LAN clients.

Optionally require a shared token:

```bash
STATS_TOKEN='choose-a-long-random-value' python3 stats_server.py --port 8282
```

## Raspberry Pi

```bash
python3 -m pip install -r requirements.txt
python3 display_client.py --url http://COMPUTER_IP:8282/stats
```

The client automatically selects a matching framebuffer. To specify one explicitly:

```bash
python3 display_client.py --display-driver framebuffer --fbdev /dev/fb1 \
  --url http://COMPUTER_IP:8282/stats
```

Tap to switch between System and Top Processes. Arrow keys, Space, and Return also work; Escape quits. Add `--desktop` for development in a desktop session.

## Automatic startup

The repository includes `stats-server.service` and `stats-display.service`. Review the paths and server address, then install each as a user service:

```bash
mkdir -p ~/.config/systemd/user
cp stats-server.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now stats-server.service
```

On the Pi, install `stats-display.service` similarly. Enable lingering if the services must start before login.

## Security

The server exposes host and process information without transport encryption. Use it only on a trusted local network. A token restricts access but does not encrypt traffic.

## License

MIT

## Raspberry Pi Zero W / Zero 2 W setup (Debian 13 Trixie Lite)

The provisioning script is intended for a headless Trixie Lite installation using the current Raspberry Pi boot layout under `/boot/firmware`. It supports the 480×320 Piscreen/FBTFT SPI display with ADS7846-compatible touchscreen used by this project.

The installer handles:

- `python3`, `python3-pygame`, `python3-numpy`, and the DejaVu font package
- `dtparam=spi=on`
- `dtoverlay=piscreen,speed=16000000,drm`
- `max_framebuffers=2`
- framebuffer and input device group membership
- installation under `/opt/statsdisplay`
- a protected `/etc/statsdisplay.env`
- a system-wide `stats-display.service` that starts at boot

On a fresh Pi, first enable SSH and make sure the host server is reachable. Copy the repository to the Pi, then run:

```bash
cd statsdisplay
chmod +x setup_pi.sh
sudo ./setup_pi.sh --server http://COMPUTER_IP:8282/stats --user USERNAME
sudo reboot
```

The username must be changed to the account created by Raspberry Pi Imager. If a token is configured on the host, pass the same value with `--token`, or set `STATS_TOKEN` in the environment.

From the host computer, deployment can be performed entirely over SSH (the Pi must allow passwordless sudo):

```bash
chmod +x deploy_pi.sh
./deploy_pi.sh --host PI_IP --user USERNAME \
  --server http://COMPUTER_IP:8282/stats
```

After deployment, verify the display on the Pi:

```bash
sudo systemctl status stats-display.service
journalctl -u stats-display.service -n 100
ls -l /dev/fb1 /dev/input/event*
```

The installer keeps a first-run backup at `/boot/firmware/config.txt.statsdisplay.bak`. Re-running it is idempotent and only replaces its marked `statsdisplay` block. Do not manually duplicate the `piscreen` overlay elsewhere in `config.txt`.

The display immediately shows each active local interface address while Wi-Fi is connecting and while the stats server is unavailable, then keeps the current IP addresses visible below the dashboard.

For a dedicated appliance that only needs Wi-Fi, SSH, and this display, add `--minimal`:

```bash
sudo ./setup_pi.sh --server http://COMPUTER_IP:8282/stats --minimal
# or from the host
./deploy_pi.sh --host PI_IP --user USERNAME \
  --server http://COMPUTER_IP:8282/stats --minimal
```

`--minimal` disables audio/ALSA, Bluetooth, camera/V4L2, console login, swap/ZRAM, cloud-init, timers, and other generated/static boot units. It retains HDMI/VC4 solely for a non-interactive IP status console. It also corrects netplan permissions that otherwise trigger repeated NetworkManager startup delays. Existing keys/configuration are backed up where applicable. A reboot is required. This profile is intentionally aggressive and is not suitable for a general-purpose Pi.

With `--minimal`, HDMI is retained as a non-interactive boot status screen. It displays the hostname and each active non-loopback IP address as interfaces come online. SSH remains available, but the HDMI virtual console has no login prompt. The 480×320 SPI dashboard continues on `/dev/fb1` independently.

For a different SPI display overlay, change the Piscreen settings in `setup_pi.sh` only after confirming the overlay name and touch controller in `/boot/firmware/overlays/`.
