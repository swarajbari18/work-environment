#!/usr/bin/env bash
# Install the vault daemon (needs root). Safe to re-run: code is replaced, policy and state are kept.
#
#   sudo ./vault/install.sh [--bank DIR] [--keep-bank] [--owner-uid N]
#
# Staging, for tests (no root, no systemd):  install.sh --root DIR --owner-uid N --bank DIR --no-systemctl
#
# What it does: copies the runtime code to /opt/wenv-vault (root-owned), creates /etc/wenv-vault/policy.json in
# DRY-RUN (nothing is killed), moves the question bank to /var/lib/wenv-vault (root-only, so answers are unreadable
# to your user and to agents), installs and starts wenv-vaultd, and checks that it answers. If it does not come up,
# it is switched off again and the bank stays where it was.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="" OWNER_UID="" BANK="" BANK_GIVEN=0 KEEP_BANK=0 NO_SYSTEMCTL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2%/}"; shift 2 ;;
    --owner-uid) OWNER_UID="$2"; shift 2 ;;
    --bank) BANK="$2"; BANK_GIVEN=1; shift 2 ;;
    --keep-bank) KEEP_BANK=1; shift ;;
    --no-systemctl) NO_SYSTEMCTL=1; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

die() { echo "install: $*" >&2; exit 1; }
say() { echo "[install] $*"; }

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  die "needs root. Run:  sudo $0"
fi

OWNER_HOME=""
if [ -z "$OWNER_UID" ]; then
  [ -n "${SUDO_USER:-}" ] || die "cannot tell whose vault this is; run through sudo or pass --owner-uid"
  OWNER_UID="$(id -u "$SUDO_USER")"
  OWNER_HOME="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
fi
case "$OWNER_UID" in ''|*[!0-9]*) die "owner uid must be a number" ;; esac
[ "$OWNER_UID" -ne 0 ] || die "the owner must be a normal user, not root"
CODE_DIR="$ROOT/opt/wenv-vault"
ETC_DIR="$ROOT/etc/wenv-vault"
STATE_DIR="$ROOT/var/lib/wenv-vault"

# Which question bank to install. A re-install (to pick up new code) has no readable bank any more, because the
# first install moved it; it then reuses the bank that is already installed.
if [ -z "$BANK" ] && [ -n "$OWNER_HOME" ]; then
  BANK="$OWNER_HOME/.local/share/wenv-vault/bank"
fi
REUSE_BANK=0
if [ -n "$BANK" ] && ls "$BANK"/*.jsonl >/dev/null 2>&1; then
  :
elif [ "$BANK_GIVEN" -eq 0 ] && ls "$STATE_DIR/bank"/*.jsonl >/dev/null 2>&1; then
  REUSE_BANK=1
  BANK="$STATE_DIR/bank"
elif [ -z "$BANK" ]; then
  die "pass --bank DIR"
elif [ ! -d "$BANK" ]; then
  die "no question bank at $BANK (build one with vault/fetch_bank.sh)"
else
  die "no .jsonl files in $BANK"
fi
RUN_DIR="$ROOT/run/wenv-vault"
UNIT="$ROOT/etc/systemd/system/wenv-vaultd.service"
RUNTIME_FILES=(policy.py questions.py engine.py matcher.py config.py protocol.py enforce.py vaultd.py
               client.py gate.py launch.py cli.py agent.py desktop.py settings.py claude_guard.py fence.py)
PYTHON=/usr/bin/python3
[ -x "$PYTHON" ] || PYTHON="$(command -v python3)"

for f in "${RUNTIME_FILES[@]}" uninstall.sh; do
  [ -f "$HERE/$f" ] || die "missing $HERE/$f"
  "$PYTHON" - "$HERE/$f" <<'PY' || die "$f does not parse; refusing to install"
import ast, sys
path = sys.argv[1]
if path.endswith(".py"):
    ast.parse(open(path, encoding="utf-8").read(), path)
PY
done

# --- 1. code ------------------------------------------------------------------------------------------------------
say "code -> $CODE_DIR"
install -d -m 0755 "$CODE_DIR"
for f in "${RUNTIME_FILES[@]}"; do install -m 0644 "$HERE/$f" "$CODE_DIR/$f"; done
install -m 0755 "$HERE/uninstall.sh" "$CODE_DIR/uninstall.sh"

# --- 2. policy (kept if present), state, bank ----------------------------------------------------------------------
install -d -m 0755 "$ETC_DIR"
install -d -m 0755 "$ROOT/etc/dconf" "$ROOT/etc/dconf/profile" "$ROOT/etc/dconf/db"   # the settings guard writes here
install -d -m 0700 "$STATE_DIR" "$STATE_DIR/bank"
if [ -f "$ETC_DIR/policy.json" ]; then
  say "policy kept: $ETC_DIR/policy.json"
else
  say "policy created (dry-run: nothing is killed until 'wenv-vault enforce on')"
  "$PYTHON" -I - "$CODE_DIR" "$OWNER_UID" "$ETC_DIR/policy.json" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import config
config.save_policy(sys.argv[3], config.default_policy(int(sys.argv[2])))
PY
fi

if [ "$REUSE_BANK" -eq 1 ]; then
  say "bank: keeping the installed one ($(ls "$STATE_DIR/bank"/*.jsonl | wc -l) file(s))"
else
  say "bank: copying $(ls "$BANK"/*.jsonl | wc -l) file(s) from $BANK"
  for f in "$BANK"/*.jsonl; do install -m 0600 "$f" "$STATE_DIR/bank/$(basename "$f")"; done
fi

say "self-test"
SELFTEST="$("$PYTHON" -I "$CODE_DIR/vaultd.py" --root "$ROOT" --owner-uid "$OWNER_UID" --selftest)" \
  || die "self-test failed: $SELFTEST"
echo "         $SELFTEST"
BANK_COUNT="$("$PYTHON" -I -c 'import json,sys; print(json.loads(sys.argv[1])["bank"])' "$SELFTEST")"
[ "$BANK_COUNT" -ge 100 ] || die "bank has only $BANK_COUNT usable questions"

# --- 3. service ---------------------------------------------------------------------------------------------------
write_unit() {   # write_unit hardened|relaxed
  install -d -m 0755 "$(dirname "$UNIT")"
  {
    cat <<UNITEOF
[Unit]
Description=Work environment vault daemon (quiz gate for Steam and Firefox)
After=local-fs.target

[Service]
Type=simple
ExecStart=$PYTHON -I $CODE_DIR/vaultd.py --owner-uid $OWNER_UID
ExecReload=/bin/kill -HUP \$MAINPID
Restart=always
RestartSec=3
RuntimeDirectory=wenv-vault
RuntimeDirectoryMode=0755
UNITEOF
    if [ "$1" = hardened ]; then
      cat <<UNITEOF
NoNewPrivileges=yes
ProtectSystem=strict
ReadWritePaths=/var/lib/wenv-vault /etc/wenv-vault /etc/dconf
PrivateTmp=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
CapabilityBoundingSet=CAP_KILL CAP_SYS_PTRACE CAP_DAC_READ_SEARCH
UNITEOF
    fi
    cat <<UNITEOF

[Install]
WantedBy=multi-user.target
UNITEOF
  } > "$UNIT"
  chmod 0644 "$UNIT"
}

healthy() {
  "$PYTHON" -I - <<'PY'
import sys
sys.path.insert(0, "/opt/wenv-vault")
import client
try:
    r = client.VaultClient("/run/wenv-vault/vaultd.sock", timeout=3).call("status")
except client.VaultUnavailable:
    sys.exit(1)
sys.exit(0 if r.get("ok") else 1)
PY
}

wait_healthy() {
  for _ in 1 2 3 4 5 6 7 8; do
    if healthy; then return 0; fi
    sleep 1
  done
  return 1
}

if [ "$NO_SYSTEMCTL" -eq 1 ]; then
  write_unit hardened
  say "staged: unit written to $UNIT, systemd not touched"
else
  MODE=hardened
  write_unit hardened
  systemctl daemon-reload
  systemctl enable wenv-vaultd.service >/dev/null 2>&1
  systemctl restart wenv-vaultd.service
  if ! wait_healthy; then
    say "did not come up with the sandboxed unit; trying without the sandbox options"
    journalctl -u wenv-vaultd.service -n 15 --no-pager 2>/dev/null | sed 's/^/         /' || true
    MODE=relaxed
    write_unit relaxed
    systemctl daemon-reload
    systemctl restart wenv-vaultd.service
    if ! wait_healthy; then
      journalctl -u wenv-vaultd.service -n 25 --no-pager 2>/dev/null | sed 's/^/         /' || true
      systemctl disable --now wenv-vaultd.service >/dev/null 2>&1 || true
      rm -f "$UNIT"
      systemctl daemon-reload
      die "the daemon did not answer; it has been switched off again. Your bank was not touched."
    fi
  fi
  say "wenv-vaultd is running ($MODE unit) and answers on /run/wenv-vault/vaultd.sock"
fi

# --- 4. the bank now lives root-only; remove the readable copy ----------------------------------------------------
if [ "$KEEP_BANK" -eq 0 ] && [ "$(cd "$BANK" && pwd)" != "$(cd "$STATE_DIR/bank" && pwd)" ]; then
  rm -f "$BANK"/*.jsonl
  say "removed the readable copy of the bank ($BANK); rebuild any time with vault/fetch_bank.sh"
fi

# The password you just typed must not stay usable by other processes in this session.
if [ -z "$ROOT" ] && [ -n "${SUDO_USER:-}" ]; then
  rm -f "/run/sudo/ts/$SUDO_USER" 2>/dev/null || true
fi

cat <<DONE

Installed in DRY-RUN. Next, as your normal user (no sudo):
  ./vault/install-user.sh          wrappers, launcher overrides and the desktop agent
  wenv-vault status                see the mode, today's state and the apps
  wenv-vault gate --practice       try the quiz window without changing anything
  wenv-vault log                   what it would have blocked
When you are happy:  wenv-vault enforce on   (free; turning it off again costs the 120-question quiz)
Optional protection for the rest of the environment (each is a separate step):
  wenv-vault settings init              lock the environment's GNOME settings (free; changing them costs 120)
  sudo ./vault/install-agent-fence.sh   managed Claude Code settings that keep agents away from the guard
  sudo ./vault/docker-off.sh            leave the docker group (use 'sudo docker' from then on)
DONE
