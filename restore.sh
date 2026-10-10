#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
export_schema_dirs

purge=0
[ "${1:-}" = "--purge" ] && purge=1

# The vault may have the settings locked; restoring would just fail key by key, or be reverted at the end of an
# unlock window. Say what to do instead.
case "$(vault_settings_state)" in
  locked)
    {
      echo "The vault has these settings locked, so they cannot be restored yet."
      echo "  To take the environment and the vault down together:"
      echo "      wenv-vault uninstall          (120 questions)"
      echo "      sudo /opt/wenv-vault/uninstall.sh"
      echo "      then run this script again"
      echo "  To only try a restore for a while:"
      echo "      wenv-vault settings unlock    (120 questions; the locked values return after 20 minutes)"
    } >&2
    exit 1
    ;;
  unlocked)
    echo "Note: the vault's unlock window is open; its values return when the window ends."
    ;;
esac

if [ ! -f "$STATE_FILE" ]; then
  echo "No backup at $STATE_FILE; nothing to restore." >&2
  exit 1
fi

failures=0
while IFS=$'\t' read -r schema key state value; do
  if [ "$state" = "SET" ]; then
    if gsettings set "$schema" "$key" "$value" 2>/dev/null; then
      printf 'restored %s %s\n' "$schema" "$key"
    else
      printf 'FAILED   %s %s\n' "$schema" "$key"
      failures=$((failures + 1))
    fi
  else
    if gsettings reset "$schema" "$key" 2>/dev/null || dconf reset "$(key_path "$schema" "$key")" 2>/dev/null; then
      printf 'reset    %s %s\n' "$schema" "$key"
    else
      printf 'FAILED   %s %s\n' "$schema" "$key"
      failures=$((failures + 1))
    fi
  fi
done < "$STATE_FILE"

if [ "$purge" -eq 1 ]; then
  pkill -x aw-tauri 2>/dev/null
  pkill -x aw-awatcher 2>/dev/null
  rm -rf "${EXT_DIR:?}/$UUID_JP" "${EXT_DIR:?}/$UUID_CBF" "${EXT_DIR:?}/$UUID_FWD" "${EXT_DIR:?}/$UUID_TODAY"
  rm -f "$HOME/.config/autostart/zwe-activitywatch.desktop"
  rm -f "$HOME/.local/bin/aw-tauri" "$HOME/.local/bin/aw-awatcher"
  rm -rf "$HOME/.local/opt/activitywatch"
  echo "Removed third-party components (task data in ~/.local/share/wenv-today is kept). ActivityWatch data (~/.local/share/activitywatch) and config (~/.config/activitywatch) were kept."
fi

[ "$failures" -gt 0 ] && echo "$failures key(s) failed to restore."
echo "Restore complete. Log out and back in to fully reload GNOME Shell."
[ "$failures" -eq 0 ]
