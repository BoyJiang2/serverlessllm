#!/bin/bash
# One-time root install of the independent debug-stack guard: a systemd timer running debug_guard.py every minute.
# Debug GPU runs refuse to start unless this timer is active and runs exactly the debug_guard.py shipped with them.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'run as root: sudo bash install_debug_guard.sh' >&2; exit 1; }
here=$(cd "$(dirname "$0")" && pwd)
dest=/usr/local/lib/sllm-debug-guard
install -d -o root -g root -m 0755 "$dest"
install -o root -g root -m 0644 "$here/debug_guard.py" "$dest/debug_guard.py"
install -d -o root -g root -m 0755 /var/lib/sllm-debug-guard  # heartbeat of the last completed sweep
cat > /etc/systemd/system/sllm-debug-guard.service <<EOF
[Unit]
Description=Remove expired DEBUG-UNREVIEWED debug-stack containers
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -I $dest/debug_guard.py
EOF
cat > /etc/systemd/system/sllm-debug-guard.timer <<EOF
[Unit]
Description=Run the debug-stack guard every minute

[Timer]
OnBootSec=60
OnUnitActiveSec=60
AccuracySec=5

[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable --now sllm-debug-guard.timer
systemctl start sllm-debug-guard.service  # one sweep now; fails here if root cannot reach Docker
sha256sum "$dest/debug_guard.py"
cat /var/lib/sllm-debug-guard/heartbeat.json
systemctl is-active sllm-debug-guard.timer
