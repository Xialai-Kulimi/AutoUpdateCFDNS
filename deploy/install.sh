#!/usr/bin/env bash
set -euo pipefail

# Ensure script is run with root privileges
if [ "${EUID}" -ne 0 ]; then
    echo "Error: install.sh must be run as root (e.g. sudo bash deploy/install.sh)" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

CFDNS_BIN="${REPO_DIR}/.venv/bin/cfdns"
SERVICE_SRC="${REPO_DIR}/deploy/cfdns.service"
SERVICE_DST="/etc/systemd/system/cfdns.service"
PPP_SRC="${REPO_DIR}/deploy/ppp-ip-up-cfdns"
PPP_DST="/etc/ppp/ip-up.d/cfdns"

echo "=== Installing cfdns v2 ==="

# Step 1: Check virtualenv binary
if [ ! -f "${CFDNS_BIN}" ]; then
    echo "Error: ${CFDNS_BIN} not found." >&2
    echo "Please build the virtualenv first as non-root user:" >&2
    echo "  cd ${REPO_DIR}" >&2
    echo "  uv venv && uv pip install -e ." >&2
    exit 1
fi

# Step 2: Symlink CLI to /usr/local/bin/cfdns
echo "-> Creating symlink /usr/local/bin/cfdns..."
ln -sf "${CFDNS_BIN}" /usr/local/bin/cfdns

# Step 3: Install systemd service unit
echo "-> Installing systemd unit ${SERVICE_DST}..."
cp "${SERVICE_SRC}" "${SERVICE_DST}"
chmod 644 "${SERVICE_DST}"
systemctl daemon-reload

# Step 4: Install ppp hook
echo "-> Installing ppp hook ${PPP_DST}..."
mkdir -p /etc/ppp/ip-up.d
cp "${PPP_SRC}" "${PPP_DST}"
chmod 755 "${PPP_DST}"

echo
echo "=== Installation complete ==="
echo "Follow these manual steps to perform production cutover:"
echo "1. Stop and disable legacy service in supervisord:"
echo "   supervisorctl stop AutoUpdateDNS"
echo "   rm /etc/supervisor/conf.d/autoUpdateDNS.conf && supervisorctl reread && supervisorctl update"
echo "2. Remove legacy ppp hook:"
echo "   rm -f /etc/ppp/ip-up.d/kulimi_tw_update"
echo "3. Start and enable cfdns systemd daemon:"
echo "   systemctl enable --now cfdns"
echo "4. Verify status:"
echo "   systemctl status cfdns"
echo "   cfdns status"
echo "   cfdns list"
