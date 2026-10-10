#!/usr/bin/env bash
# Remove the vault daemon. Needs root AND the one-time approval from the 120-question quiz:
#   wenv-vault uninstall       (as your user, passes the quiz, writes the approval)
#   sudo /opt/wenv-vault/uninstall.sh [--purge]
# --purge also deletes the policy, the used-questions state, the log and the bank.
set -euo pipefail

ROOT="${WENV_VAULT_ROOT:-}"
PURGE=0
for a in "$@"; do
  case "$a" in
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) ;;
  esac
done

die() { echo "uninstall: $*" >&2; exit 1; }
if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  die "needs root: sudo $0"
fi

TOKEN="$ROOT/run/wenv-vault/uninstall-ok"
[ -f "$TOKEN" ] || die "no approval. First run, as your user:  wenv-vault uninstall  (the 120-question quiz)"
python3 -I - "$TOKEN" <<'PY' || die "the approval has expired. Take the quiz again: wenv-vault uninstall"
import datetime, json, sys
d = json.load(open(sys.argv[1]))
if datetime.datetime.fromisoformat(d["expires"]) < datetime.datetime.now():
    sys.exit(1)
PY

# Locks and the agent fence must not outlive the daemon: nothing could unlock them afterwards.
rm -rf "$ROOT/etc/dconf/db/wenv.d" "$ROOT/etc/dconf/db/wenv"
PROFILE="$ROOT/etc/dconf/profile/user"
if [ -f "$PROFILE" ]; then
  grep -v '^system-db:wenv$' "$PROFILE" > "$PROFILE.new" || true
  if [ ! -s "$PROFILE.new" ] || [ "$(tr -d '\n' < "$PROFILE.new")" = "user-db:user" ]; then
    rm -f "$PROFILE" "$PROFILE.new"
  else
    mv "$PROFILE.new" "$PROFILE"
  fi
fi
if [ -z "$ROOT" ] && command -v dconf >/dev/null 2>&1; then dconf update 2>/dev/null || true; fi
MANAGED="$ROOT/etc/claude-code/managed-settings.json"
if [ -f "$MANAGED" ] && [ -f "$ROOT/opt/wenv-vault/fence.py" ]; then
  python3 -I - "$ROOT/opt/wenv-vault" "$MANAGED" <<'PYEOF' || echo "uninstall: could not clean $MANAGED; remove the vault entries by hand" >&2
import json, os, sys
sys.path.insert(0, sys.argv[1])
import fence
path = sys.argv[2]
with open(path) as fh:
    current = json.load(fh)
cleaned, changed = fence.unmerge(current)
if changed:
    if cleaned:
        with open(path, "w") as fh:
            json.dump(cleaned, fh, indent=2, sort_keys=True)
            fh.write("\n")
    else:
        os.unlink(path)
PYEOF
fi

if [ -z "$ROOT" ]; then
  systemctl disable --now wenv-vaultd.service 2>/dev/null || true
  rm -f /etc/systemd/system/wenv-vaultd.service
  systemctl daemon-reload
fi
rm -f "$TOKEN" "$ROOT/etc/systemd/system/wenv-vaultd.service"
rm -rf "$ROOT/opt/wenv-vault"
if [ "$PURGE" -eq 1 ]; then
  rm -rf "$ROOT/etc/wenv-vault" "$ROOT/var/lib/wenv-vault"
  echo "uninstall: removed everything, including policy, state and the bank"
else
  echo "uninstall: daemon and code removed. Kept $ROOT/etc/wenv-vault and $ROOT/var/lib/wenv-vault (use --purge to delete)."
fi
echo "uninstall: your user-level pieces remain; remove them with ./vault/install-user.sh --remove"
