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
