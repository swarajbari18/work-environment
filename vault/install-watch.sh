#!/usr/bin/env bash
# Install the log-only vault watcher as a user service. Needs no sudo and kills nothing.
#   ./install-watch.sh           install and start
#   ./install-watch.sh remove    stop and remove
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/wenv-vault-watch.service"

if [ "${1:-}" = "remove" ]; then
  systemctl --user disable --now wenv-vault-watch.service 2>/dev/null || true
  rm -f "$UNIT"
  systemctl --user daemon-reload
  echo "removed (the log in ~/.local/share/wenv-vault is kept)"
  exit 0
fi

mkdir -p "$UNIT_DIR" "$HOME/.local/share/wenv-vault"
cat > "$UNIT" <<UNITEOF
[Unit]
Description=Work environment vault: log-only watcher (observes, never kills)

[Service]
ExecStart=/usr/bin/python3 -I $HERE/watch.py run
Restart=on-failure
RestartSec=10
Nice=10

[Install]
WantedBy=default.target
UNITEOF
systemctl --user daemon-reload
systemctl --user enable --now wenv-vault-watch.service
systemctl --user --no-pager --lines=0 status wenv-vault-watch.service | head -4
echo "report: python3 $HERE/watch.py report"
