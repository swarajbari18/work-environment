#!/usr/bin/env bash
# Keeps ActivityWatch recording. The daemon starts the window watcher, but the watcher can exit
# while the daemon keeps running, which left a gap in the data. Restart the daemon when either is missing.
set -u
AW="$HOME/.local/bin/aw-tauri"
start_daemon() { setsid nohup "$AW" --daemon >/dev/null 2>&1 < /dev/null & }

if ! pgrep -x aw-tauri >/dev/null; then
  start_daemon
  echo "started: daemon was not running"
elif ! pgrep -x aw-awatcher >/dev/null; then
  pkill -x aw-tauri
  sleep 2
  start_daemon
  echo "restarted: window watcher was missing"
else
  echo "ok: daemon and watcher running"
fi
