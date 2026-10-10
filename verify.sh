#!/usr/bin/env bash
# Live verification of the work environment. Exit 0 only if every check passes.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/lib.sh"
export_schema_dirs

pass=0; fail=0
check() { # check <label> <command...>
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then printf '  PASS  %s\n' "$label"; pass=$((pass + 1))
  else printf '  FAIL  %s\n' "$label"; fail=$((fail + 1)); fi
}

vault_section() {
  echo "[vault]"
  case "$(vault_settings_state)" in
    locked)
      printf '  INFO  the vault has these settings locked\n'
      while IFS='|' read -r schema key target; do
        # filter-active stays writable on purpose (wenv-color); enabled-extensions is not in these lists
        [ "$schema|$key" = "org.gnome.shell.extensions.colorblind-filters|filter-active" ] && continue
        check "$schema $key is locked" test "$(gsettings writable "$schema" "$key" 2>/dev/null)" = "false"
      done < <(printf '%s\n' "${STAGE_NATIVE[@]}" "${STAGE_JUSTPERFECTION[@]}" "${STAGE_GRAYSCALE[@]}")
      ;;
    unlocked) printf '  INFO  the unlock window is open; run: wenv-vault settings lock  (or it reverts)\n' ;;
    *)        printf '  INFO  these settings are not locked (wenv-vault settings init)\n' ;;
  esac
}

if [ "${1:-}" = "vault" ]; then
  vault_section
  echo
  echo "$pass passed, $fail failed"
  [ "$fail" -eq 0 ]
  exit
fi

echo "[settings]"
while IFS='|' read -r schema key target; do
  current="$(gsettings get "$schema" "$key" 2>/dev/null)"
  check "$schema $key = $target" test "$current" = "$target"
done < <(printf '%s\n' "${STAGE_NATIVE[@]}" "${STAGE_JUSTPERFECTION[@]}" "${STAGE_GRAYSCALE[@]}")

vault_section

echo "[extensions]"
enabled="$(gsettings get org.gnome.shell enabled-extensions)"
for uuid in "$UUID_JP" "$UUID_CBF" "$UUID_FWD"; do
  check "$uuid enabled in settings" grep -q "'$uuid'" <<< "$enabled"
  check "$uuid ACTIVE in shell" bash -c "gnome-extensions info '$uuid' 2>/dev/null | grep -q 'State: ACTIVE'"
done
for uuid in "${ZORIN_VISUAL_EXTENSIONS[@]}"; do
  check "$uuid disabled" bash -c "! grep -q \"'$uuid'\" <<< \"$enabled\""
done
check "shell has no errors mentioning our extensions this session" bash -c \
  "test \$(journalctl --user -b -p err --no-pager -g 'just-perfection|colorblind-filters|focused-window-dbus' 2>/dev/null | grep -vc -- '-- No entries --') -eq 0"

echo "[today widget]"
same_file() { cmp -s "$1" "$2"; }
for f in metadata.json extension.js store.js scanout.js stylesheet.css; do
  check "installed $f matches source" same_file "$HERE/today/$f" "$EXT_DIR/$UUID_TODAY/$f"
done
check "store logic tests pass (gjs)" bash -c "cd '$HERE/today' && gjs -m test_store.js 2>&1 | grep -q '0 failed'"
check "scanout guard tests pass (gjs)" bash -c "cd '$HERE/today' && gjs -m test_scanout.js 2>&1 | grep -q '0 failed'"
check "ActivityWatch and file IO tests pass (gjs)" bash -c "cd '$HERE/today' && gjs -m test_io.js 2>&1 | grep -q 'ALL IO CHECKS PASSED'"
check "colorblind top-bar button removed from installed copy" grep -q "WENV: no top-bar button" "$EXT_DIR/colorblind-filters@G-dH.github.com/extension.js"
check "wenv-today ACTIVE in shell" bash -c "gnome-extensions info '$UUID_TODAY' 2>/dev/null | grep -q 'State: ACTIVE'"
check "wenv-today has no shell errors" bash -c "test \"\$(busctl --user call org.gnome.Shell.Extensions /org/gnome/Shell/Extensions org.gnome.Shell.Extensions GetExtensionErrors s '$UUID_TODAY' 2>/dev/null)\" = 'as 0'"

echo "[activitywatch]"
check "aw-tauri daemon running" pgrep -x aw-tauri
check "aw-awatcher running" pgrep -x aw-awatcher
check "ActivityWatch watchdog timer active" systemctl --user is-active --quiet wenv-aw-watchdog.timer
check "server answers on 127.0.0.1:5600" curl -fsS --max-time 3 http://127.0.0.1:5600/api/0/info
buckets="$(curl -fsS --max-time 3 http://127.0.0.1:5600/api/0/buckets/ 2>/dev/null)"
check "window bucket exists" bash -c "grep -q 'aw-watcher-window_' <<< '$buckets'"
check "AFK bucket exists" bash -c "grep -q 'aw-watcher-afk_' <<< '$buckets'"
last="$(curl -fsS --max-time 3 "http://127.0.0.1:5600/api/0/buckets/$(python3 -c 'import sys,json; print([k for k in json.load(sys.stdin) if k.startswith("aw-watcher-window_")][0])' <<< "$buckets")/events?limit=1" 2>/dev/null)"
check "window events are recent (<120 s old)" python3 -c '
import sys, json, datetime
ev = json.loads(sys.argv[1])
ts = datetime.datetime.fromisoformat(ev[0]["timestamp"].replace("Z", "+00:00"))
age = (datetime.datetime.now(datetime.timezone.utc) - ts).total_seconds()
sys.exit(0 if age < 120 else 1)' "$last"

echo
echo "$pass passed, $fail failed"
[ "$fail" -eq 0 ]
