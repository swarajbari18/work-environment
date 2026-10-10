#!/usr/bin/env bash
# User-level half of the vault (no sudo): command wrappers, launcher overrides, and the desktop agent.
#
#   ./vault/install-user.sh              install
#   ./vault/install-user.sh --remove     undo (only what this script wrote)
#
# Options for tests/staging: --code DIR (default /opt/wenv-vault)  --no-systemctl
#
# Overrides only make the quiz appear *before* an app opens. The daemon stops a locked app however it was
# started, so removing these never weakens the vault.
set -euo pipefail

CODE=/opt/wenv-vault
REMOVE=0
NO_SYSTEMCTL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --code) CODE="$2"; shift 2 ;;
    --remove) REMOVE=1; shift ;;
    --no-systemctl) NO_SYSTEMCTL=1; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

BIN="${WENV_BIN_DIR:-$HOME/.local/bin}"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/wenv-vault-agent.service"
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"
# app name in the vault -> desktop entries that start it
ENTRIES=("steam:steam" "steam:Forza Horizon 5" "firefox:firefox_firefox")

[ -f "$CODE/desktop.py" ] || { echo "no vault code at $CODE; run sudo ./vault/install.sh first" >&2; exit 1; }

desktop_op() {   # desktop_op install|remove NAME DESKTOP_ID
  "$PYTHON" -I - "$CODE" "$1" "$2" "$3" "$BIN/wenv-launch" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import desktop
op, name, did, launcher = sys.argv[2:6]
try:
    if op == "install":
        print("  launcher wrapped:", desktop.install_override(did, name, launcher))
    elif desktop.remove_override(did):
        print("  launcher restored:", did)
except FileNotFoundError:
    print("  (no desktop entry named", did + "; skipped)")
except (OSError, ValueError) as exc:
    print("  could not handle", did + ":", exc)
PY
}

if [ "$REMOVE" -eq 1 ]; then
  for e in "${ENTRIES[@]}"; do desktop_op remove "${e%%:*}" "${e#*:}"; done
  if [ "$NO_SYSTEMCTL" -eq 0 ]; then
    systemctl --user disable --now wenv-vault-agent.service 2>/dev/null || true
  fi
  rm -f "$UNIT" "$BIN/wenv-vault" "$BIN/wenv-launch" "$BIN/wenv-gate"
  [ "$NO_SYSTEMCTL" -eq 1 ] || systemctl --user daemon-reload
  echo "removed the user-level vault pieces"
  exit 0
fi

mkdir -p "$BIN" "$UNIT_DIR"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
wrapper() {   # wrapper NAME SCRIPT
  cat > "$BIN/$1" <<WRAP
#!/bin/sh
# Where the environment's settings file lives (the folder this project was cloned to).
: "\${WENV_LIB:=$REPO/lib.sh}"
export WENV_LIB
exec $PYTHON -I $CODE/$2 "\$@"
WRAP
  chmod 0755 "$BIN/$1"
}
wrapper wenv-vault cli.py
wrapper wenv-launch launch.py
wrapper wenv-gate gate.py
echo "wrappers in $BIN: wenv-vault, wenv-launch, wenv-gate"

echo "launchers:"
for e in "${ENTRIES[@]}"; do desktop_op install "${e%%:*}" "${e#*:}"; done

cat > "$UNIT" <<UNITEOF
[Unit]
Description=Work environment vault: desktop agent (quiz and warning windows)
After=graphical-session.target
PartOf=graphical-session.target

[Service]
ExecStart=$PYTHON -I $CODE/agent.py
Restart=always
RestartSec=3

[Install]
WantedBy=graphical-session.target
UNITEOF

if [ "$NO_SYSTEMCTL" -eq 1 ]; then
  echo "staged: agent unit written to $UNIT"
else
  systemctl --user daemon-reload
  systemctl --user enable --now wenv-vault-agent.service
  echo "agent: $(systemctl --user is-active wenv-vault-agent.service)"
fi
echo
echo "Try:  wenv-vault status     wenv-vault gate --practice"
case ":$PATH:" in *":$BIN:"*) ;; *) echo "note: $BIN is not on your PATH in this shell" ;; esac
