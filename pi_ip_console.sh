#!/usr/bin/env bash
# Minimal HDMI status display for a headless framebuffer appliance.
set -u

TTY=/dev/tty1
INTERVAL=${IP_DISPLAY_INTERVAL:-1}

wait_for_tty() {
    while [[ ! -c $TTY ]]; do
        sleep 0.2
    done
}

addresses() {
    ip -o -4 address show 2>/dev/null \
        | awk '$2 != "lo" {split($4, a, "/"); print $2 "  " a[1]}'
}

wait_for_tty
while :; do
    mapfile -t rows < <(addresses)
    {
        printf '\033[2J\033[H\033[?25l'
        printf 'STATSDISPLAY NETWORK\n'
        printf '===================\n\n'
        if ((${#rows[@]})); then
            printf 'ACTIVE IP ADDRESSES\n\n'
            printf '  %s\n' "${rows[@]}"
        else
            printf 'WAITING FOR NETWORK...\n'
        fi
        printf '\nSSH: port 22\n'
        printf 'Hostname: %s\n' "$(hostname)"
    } > "$TTY"
    sleep "$INTERVAL"
done
