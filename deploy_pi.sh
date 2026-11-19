#!/usr/bin/env bash
# Deploy and run setup_pi.sh on a Trixie Lite Raspberry Pi over SSH.
set -euo pipefail

HOST=${PI_HOST:-192.168.1.146}
USER=${PI_USER:-neonlightning}
SERVER_URL=${STATS_SERVER_URL:-http://192.168.1.53:8282/stats}
TOKEN=${STATS_TOKEN:-}
REMOTE_DIR=/tmp/statsdisplay-setup
SKIP_PACKAGES=0
MINIMAL=0

usage() {
    cat <<'EOF'
Usage: ./deploy_pi.sh [options]

Options:
  --host HOST        Pi hostname/IP (default: 192.168.1.146 or PI_HOST)
  --user USER        SSH/runtime user (default: neonlightning or PI_USER)
  --server URL       Stats endpoint (default: http://192.168.1.53:8282/stats)
  --token TOKEN      Optional shared token (prefer STATS_TOKEN environment variable)
  --skip-packages    Skip apt on the Pi
  --minimal          Apply aggressive display-appliance service/hardware cleanup
  -h, --help         Show help
EOF
}

while (($#)); do
    case "$1" in
        --host) HOST=${2:?}; shift 2 ;;
        --user) USER=${2:?}; shift 2 ;;
        --server) SERVER_URL=${2:?}; shift 2 ;;
        --token) TOKEN=${2:?}; shift 2 ;;
        --skip-packages) SKIP_PACKAGES=1; shift ;;
        --minimal) MINIMAL=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"
for file in display_client.py setup_pi.sh minimize_pi.sh; do
    [[ -f $file ]] || { echo "Missing $file" >&2; exit 1; }
done
ssh -o BatchMode=yes -o ConnectTimeout=10 "$USER@$HOST" "rm -rf '$REMOTE_DIR' && install -d -m 0700 '$REMOTE_DIR'"
scp -q display_client.py setup_pi.sh minimize_pi.sh "$USER@$HOST:$REMOTE_DIR/"
ssh -o BatchMode=yes "$USER@$HOST" "chmod 0755 '$REMOTE_DIR/setup_pi.sh' '$REMOTE_DIR/minimize_pi.sh'"
quoted_server=$(printf '%q' "$SERVER_URL")
quoted_token=$(printf '%q' "$TOKEN")
args="--server $quoted_server --user $(printf '%q' "$USER")"
[[ -z $TOKEN ]] || args+=" --token $quoted_token"
((SKIP_PACKAGES == 0)) || args+=" --skip-packages"
((MINIMAL == 0)) || args+=" --minimal"
ssh -o BatchMode=yes "$USER@$HOST" "cd '$REMOTE_DIR' && chmod +x setup_pi.sh && sudo -n ./setup_pi.sh $args"
ssh -o BatchMode=yes "$USER@$HOST" "rm -rf '$REMOTE_DIR'"
echo "Deployment to $USER@$HOST complete."
