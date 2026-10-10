#!/usr/bin/env bash
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
export_schema_dirs

usage() {
  cat <<USAGE
Usage: apply.sh <stage>

  instrument       enable the Focused Window D-Bus extension (needed by ActivityWatch on GNOME Wayland)
  native           Level 1-2: Zorin/GNOME native settings, visual Zorin extensions off
  justperfection   Level 3: Just Perfection (hide panel and dash)
  grayscale        Level 4: Colorblind Filters, desaturation at maximum
  today            Today widget on the desktop background (day/year progress, task list)
  timers           the ActivityWatch watchdog (every 5 min) and the hourly report, as user systemd timers
  all              native, justperfection, grayscale, today, timers (then instrument)
  status           compare current settings with the target values
USAGE
}

fails=0
locked_hint=0

set_key() {
  local schema="$1" key="$2" value="$3" err current
  # A key the vault has locked cannot be written, even with the same value. If it already holds the target there
  # is nothing to do, so re-applying stays harmless while the lock is on.
  if [ "$(gsettings writable "$schema" "$key" 2>/dev/null)" = "false" ]; then
    current="$(gsettings get "$schema" "$key" 2>/dev/null)"
    if [ "$current" = "$value" ]; then
      printf '  ok    %s %s = %s (locked, already correct)\n' "$schema" "$key" "$value"
      return
    fi
  fi
  if err="$(gsettings set "$schema" "$key" "$value" 2>&1)"; then
    printf '  ok    %s %s = %s\n' "$schema" "$key" "$value"
  else
    printf '  FAIL  %s %s = %s\n        %s\n' "$schema" "$key" "$value" "$err"
    fails=$((fails + 1))
    case "$err" in *[Ww]ritable*) locked_hint=1 ;; esac
  fi
}

apply_stage() {
  local entry schema key value
  for entry in "$@"; do
    IFS='|' read -r schema key value <<< "$entry"
    set_key "$schema" "$key" "$value"
  done
}

update_enabled() {
  local current updated
  current="$(gsettings get org.gnome.shell enabled-extensions)"
  updated="$(python3 - "$current" "$1" "$2" <<'PY'
import ast, sys
current = ast.literal_eval(sys.argv[1].replace('@as ', ''))
add = sys.argv[2].split()
remove = set(sys.argv[3].split())
result = [uuid for uuid in current if uuid not in remove]
for uuid in add:
    if uuid not in result:
        result.append(uuid)
print(repr(result))
PY
)"
  set_key org.gnome.shell enabled-extensions "$updated"
}

require_extension() {
  if [ ! -f "$EXT_DIR/$1/metadata.json" ]; then
    echo "Extension $1 is not installed in $EXT_DIR; run install-components.sh first." >&2
    exit 1
  fi
}

require_backup() {
  # Always run: creates the backup if missing, and records keys added since the last backup before they are written.
  "$HERE/backup.sh" || exit 1
}

stage_today() {
  echo "[today]"
  local dir="$EXT_DIR/$UUID_TODAY"
  mkdir -p "$dir"
  cp "$HERE/today/metadata.json" "$HERE/today/extension.js" "$HERE/today/store.js" "$HERE/today/scanout.js" "$HERE/today/stylesheet.css" "$dir/"
  update_enabled "$UUID_TODAY" ""
}

stage_timers() {
  echo "[timers]"
  local unit_dir="${WENV_UNIT_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}" unit
  mkdir -p "$unit_dir" "$WENV_HOME/reports"
  for unit in wenv-aw-watchdog.service wenv-aw-watchdog.timer wenv-report.service wenv-report.timer; do
    # The units name this folder, wherever it was cloned to.
    sed "s|@REPO@|$HERE|g" "$HERE/systemd/$unit" > "$unit_dir/$unit"
    printf '  ok    %s\n' "$unit_dir/$unit"
  done
  if [ "${WENV_NO_SYSTEMCTL:-0}" = 1 ]; then
    echo "  (systemd not touched)"
    return
  fi
  if systemctl --user daemon-reload && systemctl --user enable --now wenv-aw-watchdog.timer wenv-report.timer >/dev/null 2>&1; then
    echo "  ok    timers enabled and started"
  else
    echo "  FAIL  could not enable the timers (is a user systemd session running?)"
    fails=$((fails + 1))
  fi
}

stage_instrument() {
  require_extension "$UUID_FWD"
  echo "[instrument]"
  update_enabled "$UUID_FWD" ""
}

stage_native() {
  echo "[native]"
  apply_stage "${STAGE_NATIVE[@]}"
  update_enabled "" "${ZORIN_VISUAL_EXTENSIONS[*]}"
}

stage_justperfection() {
  require_extension "$UUID_JP"
  echo "[justperfection]"
  apply_stage "${STAGE_JUSTPERFECTION[@]}"
  update_enabled "$UUID_JP" ""
}

stage_grayscale() {
  require_extension "$UUID_CBF"
  echo "[grayscale]"
  apply_stage "${STAGE_GRAYSCALE[@]}"
  update_enabled "$UUID_CBF" ""
}

show_status() {
  local entry schema key target current mark
  for entry in "${STAGE_NATIVE[@]}" "${STAGE_JUSTPERFECTION[@]}" "${STAGE_GRAYSCALE[@]}"; do
    IFS='|' read -r schema key target <<< "$entry"
    current="$(gsettings get "$schema" "$key" 2>/dev/null || echo '(unavailable)')"
    [ "$current" = "$target" ] && mark="ok  " || mark="DIFF"
    printf '%s %s %s = %s (target %s)\n' "$mark" "$schema" "$key" "$current" "$target"
  done
  echo
  echo "enabled-extensions: $(gsettings get org.gnome.shell enabled-extensions)"
  echo
  echo "ActivityWatch:"
  if curl -fsS --max-time 3 http://127.0.0.1:5600/api/0/info 2>/dev/null; then
    echo
    curl -fsS --max-time 3 http://127.0.0.1:5600/api/0/buckets/ 2>/dev/null | python3 -c 'import sys,json; [print("  bucket:", name) for name in json.load(sys.stdin)]'
  else
    echo "  server not reachable on 127.0.0.1:5600"
  fi
}

stage="${1:-}"
case "$stage" in
  instrument)      require_backup; stage_instrument ;;
  native)          require_backup; stage_native ;;
  justperfection)  require_backup; stage_justperfection ;;
  grayscale)       require_backup; stage_grayscale ;;
  today)           require_backup; stage_today ;;
  timers)          stage_timers ;;
  all)             require_backup; stage_native; stage_justperfection; stage_grayscale; stage_today; stage_timers; stage_instrument ;;
  status)          show_status; exit 0 ;;
  *)               usage; exit 2 ;;
esac

if [ "$fails" -gt 0 ]; then
  echo "$fails setting(s) failed."
  if [ "$locked_hint" -eq 1 ]; then
    echo "Some of them are locked by the vault. To change them on purpose:"
    echo "  wenv-vault settings unlock     (120 questions; opens a 20-minute window)"
    echo "  ./apply.sh ...                 (the change)"
    echo "  wenv-vault settings lock       (keeps it; otherwise the old values come back)"
  fi
  exit 1
fi
echo "Done. Changes apply now; run verify.sh to check the live state."
