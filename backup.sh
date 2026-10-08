#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"

force=0
[ "${1:-}" = "--force" ] && force=1
if [ -f "$STATE_FILE" ] && [ "$force" -eq 0 ]; then
  # Existing backup: keep the original values, but record any keys added to lib.sh since.
  # Only call this before a stage has written the new key, or the recorded value will not be original.
  added=0
  while IFS='|' read -r schema key _; do
    if ! awk -F'\t' -v s="$schema" -v k="$key" '$1==s && $2==k {found=1} END {exit !found}' "$STATE_FILE"; then
      value="$(dconf read "$(key_path "$schema" "$key")" 2>/dev/null)"
      if [ -n "$value" ]; then
        printf '%s\t%s\tSET\t%s\n' "$schema" "$key" "$value" >> "$STATE_FILE"
      else
        printf '%s\t%s\tUNSET\t\n' "$schema" "$key" >> "$STATE_FILE"
      fi
      added=$((added + 1))
    fi
  done < <(all_keys)
  cp "$HERE/lib.sh" "$WENV_HOME/lib.sh"
  cp "$HERE/restore.sh" "$WENV_HOME/restore.sh"
  chmod +x "$WENV_HOME/restore.sh"
  echo "Backup exists at $STATE_FILE; recorded $added new key(s). Original values kept (use --force to re-record)."
  exit 0
fi

umask 077
mkdir -p "$WENV_HOME/backup"
cd "$WENV_HOME/backup" || exit 1

tmp="$(mktemp state.tsv.XXXXXX)"
while IFS='|' read -r schema key _; do
  value="$(dconf read "$(key_path "$schema" "$key")" 2>/dev/null)"
  if [ -n "$value" ]; then
    printf '%s\t%s\tSET\t%s\n' "$schema" "$key" "$value" >> "$tmp"
  else
    printf '%s\t%s\tUNSET\t\n' "$schema" "$key" >> "$tmp"
  fi
done < <(all_keys)
mv "$tmp" state.tsv

dconf dump / > dconf-full.ini 2>/dev/null
dconf dump /com/zorin/ > zorin-appearance.ini 2>/dev/null
dconf dump /org/gnome/desktop/notifications/ > notifications.ini 2>/dev/null
dconf dump /org/gnome/desktop/background/ > wallpaper.ini 2>/dev/null
{
  echo "gtk-theme:    $(gsettings get org.gnome.desktop.interface gtk-theme 2>/dev/null)"
  echo "icon-theme:   $(gsettings get org.gnome.desktop.interface icon-theme 2>/dev/null)"
  echo "cursor-theme: $(gsettings get org.gnome.desktop.interface cursor-theme 2>/dev/null)"
  echo "shell-theme:  $(gsettings get org.gnome.shell.extensions.user-theme name 2>/dev/null)"
  echo "color-scheme: $(gsettings get org.gnome.desktop.interface color-scheme 2>/dev/null)"
} > themes.txt
gnome-extensions list --details > extensions.txt 2>/dev/null
gsettings get org.gnome.shell enabled-extensions > enabled-extensions.txt 2>/dev/null
gsettings get org.gnome.shell disabled-extensions > disabled-extensions.txt 2>/dev/null
rm -rf autostart gtk-4.0 gtk-3.0
[ -d "$HOME/.config/autostart" ] && cp -a "$HOME/.config/autostart" autostart
[ -d "$HOME/.config/gtk-4.0" ] && cp -a "$HOME/.config/gtk-4.0" gtk-4.0
[ -d "$HOME/.config/gtk-3.0" ] && cp -a "$HOME/.config/gtk-3.0" gtk-3.0
{
  date -Is
  . /etc/os-release && echo "$PRETTY_NAME"
  gnome-shell --version 2>/dev/null
  echo "session: ${XDG_SESSION_TYPE:-unknown}"
} > environment.txt

cp "$HERE/lib.sh" "$WENV_HOME/lib.sh"
cp "$HERE/restore.sh" "$WENV_HOME/restore.sh"
chmod +x "$WENV_HOME/restore.sh"

echo "Backup written to $WENV_HOME/backup ($(wc -l < state.tsv) keys recorded)."
