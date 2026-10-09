#!/usr/bin/env bash
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"

DOWNLOAD_DIR="${WENV_DOWNLOAD_DIR:-$HERE/downloads}"
BIN_DIR="${WENV_BIN_DIR:-$HOME/.local/bin}"
OPT_DIR="${WENV_OPT_DIR:-$HOME/.local/opt/activitywatch}"
AUTOSTART_DIR="${WENV_AUTOSTART_DIR:-$HOME/.config/autostart}"
AW_CONFIG_DIR="${WENV_AW_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/activitywatch/aw-tauri}"

ARTIFACTS=(
  "just-perfection-v37.zip|c54ccd627540d86e6baf06c699f624ba7d5229aedfc8b00b0dc7182c6538eda8|https://extensions.gnome.org/download-extension/just-perfection-desktop@just-perfection.shell-extension.zip?version_tag=74466"
  "colorblind-filters-v22.zip|f90e2227d9da65dff03d6dd56fdaef34fc8c82019c35a91f48f856ac54d0b3ab|https://extensions.gnome.org/download-extension/colorblind-filters@G-dH.github.com.shell-extension.zip?version_tag=65559"
  "focused-window-dbus-v8.zip|2c9ca5d737f7fe64197f44727d5391a5fcac71ebafe87b820967a844df716770|https://extensions.gnome.org/download-extension/focused-window-dbus@flexagoon.com.shell-extension.zip?version_tag=62865"
  "activitywatch-tauri-v0.14.0-linux-x86_64.deb|56f68bbe6a4b37b254f4c254a0182f546eafbfee409ee87dbae01b29c0662666|https://github.com/ActivityWatch/activitywatch/releases/download/v0.14.0/activitywatch-tauri-v0.14.0-linux-x86_64.deb"
  "aw-awatcher.zip|8cdbb99916800bc509afc28785d85d5a1f74fdcea0d378d8019e2244df507eca|https://github.com/2e3s/awatcher/releases/download/v0.4.0/aw-awatcher.zip"
)

fetch_verified() {
  local name="$1" sha="$2" url="$3" path="$DOWNLOAD_DIR/$1"
  mkdir -p "$DOWNLOAD_DIR"
  if [ -f "$path" ] && echo "$sha  $path" | sha256sum -c --status -; then
    echo "  cached   $name"
    return 0
  fi
  echo "  download $name"
  curl -fsSL --max-time 600 -o "$path.part" "$url"
  if ! echo "$sha  $path.part" | sha256sum -c --status -; then
    rm -f "$path.part"
    echo "  CHECKSUM MISMATCH for $name; refusing to install." >&2
    return 1
  fi
  mv "$path.part" "$path"
}

install_extension() {
  local zip="$1" uuid="$2" target="$EXT_DIR/$2"
  rm -rf "$target"
  mkdir -p "$target"
  unzip -q "$DOWNLOAD_DIR/$zip" -d "$target"
  [ -d "$target/schemas" ] && glib-compile-schemas "$target/schemas"
  python3 - "$target/metadata.json" <<'PY'
import json, sys
meta = json.load(open(sys.argv[1]))
print("  installed", meta["uuid"], "version", meta.get("version"), "shell", ",".join(meta.get("shell-version", [])))
PY
}

install_activitywatch() {
  mkdir -p "$BIN_DIR" "$OPT_DIR" "$AUTOSTART_DIR" "$AW_CONFIG_DIR"

  rm -rf "$OPT_DIR/tauri-pkg"
  mkdir -p "$OPT_DIR/tauri-pkg"
  dpkg-deb -x "$DOWNLOAD_DIR/activitywatch-tauri-v0.14.0-linux-x86_64.deb" "$OPT_DIR/tauri-pkg"
  install -m 0755 "$OPT_DIR/tauri-pkg/usr/bin/aw-tauri" "$BIN_DIR/aw-tauri"

  rm -rf "$OPT_DIR/awatcher-pkg"
  mkdir -p "$OPT_DIR/awatcher-pkg"
  unzip -q "$DOWNLOAD_DIR/aw-awatcher.zip" -d "$OPT_DIR/awatcher-pkg"
  install -m 0755 "$OPT_DIR/awatcher-pkg/aw-awatcher" "$BIN_DIR/aw-awatcher"

  if [ ! -f "$AW_CONFIG_DIR/config.toml" ]; then
    cat > "$AW_CONFIG_DIR/config.toml" <<CONF
port = 5600
discovery_paths = [
  "$BIN_DIR",
]

[autostart]
enabled = false
minimized = true
modules = [
  "aw-awatcher",
]

[updates]
auto_download = false
CONF
    echo "  wrote    $AW_CONFIG_DIR/config.toml"
  else
    echo "  kept     existing $AW_CONFIG_DIR/config.toml"
  fi

  cat > "$AUTOSTART_DIR/zwe-activitywatch.desktop" <<DESK
[Desktop Entry]
Type=Application
Name=ActivityWatch (background)
Comment=Headless ActivityWatch server and awatcher; no tray icon, no window
Exec=$BIN_DIR/aw-tauri --daemon
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
DESK
  echo "  wrote    $AUTOSTART_DIR/zwe-activitywatch.desktop"
}

echo "[artifacts]"
for entry in "${ARTIFACTS[@]}"; do
  IFS='|' read -r name sha url <<< "$entry"
  fetch_verified "$name" "$sha" "$url"
done

# Colorblind Filters adds an eye icon to the top bar; clicking it turns the grayscale off.
# Keep the extension (it still applies the filter) but do not add that button.
# The filter is switched with wenv-color instead.
patch_colorblind_panel() {
  python3 - "$EXT_DIR/$UUID_CBF/extension.js" <<'PY'
import sys
path = sys.argv[1]
src = open(path, encoding='utf-8').read()
old = "        Main.panel.addToStatusArea('ColorBlindFilters', this._menu, 0, 'right');"
new = "        // WENV: no top-bar button; switch the filter with wenv-color on|off"
if src.count(old) != 1:
    sys.exit('patch anchor not found exactly once in ' + path)
open(path, 'w', encoding='utf-8').write(src.replace(old, new))
print('  patched  ' + path + ' (no top-bar button)')
PY
}

echo "[gnome extensions] -> $EXT_DIR"
mkdir -p "$EXT_DIR"
install_extension just-perfection-v37.zip "$UUID_JP"
install_extension colorblind-filters-v22.zip "$UUID_CBF"
patch_colorblind_panel
install_extension focused-window-dbus-v8.zip "$UUID_FWD"

echo "[activitywatch]"
install_activitywatch

echo "Done. Nothing was enabled; run apply.sh to enable components."
