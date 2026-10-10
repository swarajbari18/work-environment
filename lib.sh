#!/usr/bin/env bash
WENV_HOME="${WENV_HOME:-$HOME/work-environment-backup}"
EXT_DIR="${WENV_EXT_DIR:-$HOME/.local/share/gnome-shell/extensions}"
STATE_FILE="$WENV_HOME/backup/state.tsv"

UUID_JP='just-perfection-desktop@just-perfection'
UUID_CBF='colorblind-filters@G-dH.github.com'
UUID_FWD='focused-window-dbus@flexagoon.com'
UUID_TODAY='wenv-today@local'

ZORIN_VISUAL_EXTENSIONS=(
  zorin-taskbar@zorinos.com
  zorin-desktop-icons@zorinos.com
  zorin-desktop-cube@zorinos.com
  zorin-window-move-effect@zorinos.com
  zorin-magic-lamp-effect@zorinos.com
  zorin-spatial-window-switcher@zorinos.com
)

STAGE_NATIVE=(
  "org.gnome.desktop.interface|gtk-theme|'ZorinGrey-Dark'"
  "org.gnome.desktop.interface|icon-theme|'ZorinGrey-Dark'"
  "org.gnome.desktop.interface|color-scheme|'prefer-dark'"
  "org.gnome.desktop.interface|enable-animations|false"
  "org.gnome.shell.extensions.user-theme|name|'ZorinGrey-Dark'"
  "org.gnome.desktop.background|picture-options|'none'"
  "org.gnome.desktop.background|picture-uri|''"
  "org.gnome.desktop.background|picture-uri-dark|''"
  "org.gnome.desktop.background|primary-color|'#000000'"
  "org.gnome.desktop.background|secondary-color|'#000000'"
  "org.gnome.desktop.background|color-shading-type|'solid'"
  "org.gnome.desktop.background|show-desktop-icons|false"
  "org.gnome.desktop.screensaver|picture-options|'none'"
  "org.gnome.desktop.screensaver|picture-uri|''"
  "org.gnome.desktop.screensaver|primary-color|'#000000'"
  "org.gnome.desktop.screensaver|secondary-color|'#000000'"
  "org.gnome.desktop.notifications|show-banners|false"
  "org.gnome.desktop.notifications|show-in-lock-screen|false"
  "org.gnome.desktop.search-providers|disabled|['org.gnome.Software.desktop', 'org.gnome.Weather.desktop']"
  "org.gnome.shell.keybindings|toggle-application-view|@as []"
  "org.gnome.shell|favorite-apps|@as []"
)

STAGE_JUSTPERFECTION=(
  "org.gnome.shell.extensions.just-perfection|panel|false"
  "org.gnome.shell.extensions.just-perfection|panel-in-overview|true"
  "org.gnome.shell.extensions.just-perfection|dash|false"
  "org.gnome.shell.extensions.just-perfection|startup-status|0"
  "org.gnome.shell.extensions.just-perfection|workspace-popup|false"
  "org.gnome.shell.extensions.just-perfection|double-super-to-appgrid|false"
  "org.gnome.shell.extensions.just-perfection|show-apps-button|false"
  "org.gnome.shell.extensions.just-perfection|animation|0"
  "org.gnome.shell.extensions.just-perfection|support-notifier-type|0"
)

STAGE_GRAYSCALE=(
  "org.gnome.shell.extensions.colorblind-filters|filter-name|'Desaturation'"
  "org.gnome.shell.extensions.colorblind-filters|filter-strength|100"
  "org.gnome.shell.extensions.colorblind-filters|filter-active|true"
)

# State of the vault's lock on these settings: locked | unlocked | none (no vault, no daemon, or never locked).
vault_settings_state() {
  local cli out
  cli="$(command -v wenv-vault 2>/dev/null || true)"
  if [ -z "$cli" ] && [ -x "$HOME/.local/bin/wenv-vault" ]; then cli="$HOME/.local/bin/wenv-vault"; fi
  if [ -z "$cli" ]; then echo none; return; fi
  out="$("$cli" settings status 2>/dev/null)" || { echo none; return; }
  case "$out" in
    *UNLOCKED*) echo unlocked ;;
    *LOCKED*)   echo locked ;;
    *)          echo none ;;
  esac
}

all_keys() {
  printf '%s\n' "${STAGE_NATIVE[@]}" "${STAGE_JUSTPERFECTION[@]}" "${STAGE_GRAYSCALE[@]}"
  printf '%s\n' "org.gnome.shell|enabled-extensions|"
}

key_path() {
  local schema_path="${1//./\/}"
  printf '/%s/%s' "$schema_path" "$2"
}

export_schema_dirs() {
  local dirs="" uuid
  for uuid in "$UUID_JP" "$UUID_CBF" "$UUID_FWD" "$UUID_TODAY"; do
    [ -d "$EXT_DIR/$uuid/schemas" ] && dirs="$dirs:$EXT_DIR/$uuid/schemas"
  done
  if [ -n "$dirs" ]; then
    export GSETTINGS_SCHEMA_DIR="${dirs#:}${GSETTINGS_SCHEMA_DIR:+:$GSETTINGS_SCHEMA_DIR}"
  fi
}
