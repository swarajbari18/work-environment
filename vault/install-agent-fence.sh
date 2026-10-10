#!/usr/bin/env bash
# Install the managed Claude Code settings that keep agents away from the vault's guard (needs root).
#
#   sudo ./vault/install-agent-fence.sh [--remove]
#   Staging, for tests: --root DIR
#
# Writes /etc/claude-code/managed-settings.json: deny rules for the guard paths and for sudo/pkexec/su/chattr, plus
# a PreToolUse hook (/opt/wenv-vault/claude_guard.py). Existing content is merged, never replaced; the old file is
# kept next to it. It does NOT turn on the sandbox and does NOT set allowManagedHooksOnly, so your own hooks
# (the notify-send ones) keep working. Applies to Claude Code sessions started afterwards.
set -euo pipefail

ROOT="" REMOVE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --root) ROOT="${2%/}"; shift 2 ;;
    --remove) REMOVE=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
die() { echo "install-agent-fence: $*" >&2; exit 1; }
[ -n "$ROOT" ] || [ "$(id -u)" -eq 0 ] || die "needs root: sudo $0"

CODE="$ROOT/opt/wenv-vault"
FILE="$ROOT/etc/claude-code/managed-settings.json"
[ -f "$CODE/claude_guard.py" ] && [ -f "$CODE/fence.py" ] || die "the vault is not installed here; run: sudo ./vault/install.sh"

python3 -I - "$CODE" "$FILE" "$REMOVE" <<'PYEOF'
import json, os, shutil, sys, time
code, path, remove = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
sys.path.insert(0, code)
import fence

current = None
if os.path.exists(path):
    try:
        with open(path, encoding="utf-8") as fh:
            current = json.load(fh)
    except ValueError as exc:
        sys.exit(f"install-agent-fence: {path} is not valid JSON ({exc}); fix or move it first")

if remove:
    cleaned, changed = fence.unmerge(current)
    if not changed:
        print("nothing of the vault's was in", path)
        sys.exit(0)
    if cleaned:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(cleaned, fh, indent=2, sort_keys=True)
            fh.write("\n")
    else:
        os.unlink(path)
    print("removed the vault's rules from", path)
    sys.exit(0)

try:
    text = fence.render(current)
except ValueError as exc:
    sys.exit(f"install-agent-fence: cannot merge into {path}: {exc}")
os.makedirs(os.path.dirname(path), exist_ok=True)
if current is not None:
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    print("kept the previous file as", backup)
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    fh.write(text)
os.chmod(tmp, 0o644)
os.replace(tmp, path)
with open(path, encoding="utf-8") as fh:
    json.load(fh)                                   # re-read: never leave a file Claude Code could not parse
print("wrote", path)
PYEOF

if [ "$REMOVE" -eq 0 ]; then
  cat <<DONE
Done. New Claude Code sessions will:
  - refuse Edit/Write on /etc/wenv-vault, /opt/wenv-vault, /var/lib/wenv-vault, /run/wenv-vault, /etc/dconf, /etc/claude-code
  - refuse sudo, pkexec, su and chattr
  - run the vault's hook on Bash/Edit/Write/NotebookEdit calls (refuses spelled-out guard paths and vault changes)
Undo: sudo ./vault/install-agent-fence.sh --remove
DONE
fi
if [ -z "$ROOT" ] && [ -n "${SUDO_USER:-}" ]; then rm -f "/run/sudo/ts/$SUDO_USER" 2>/dev/null || true; fi
