#!/usr/bin/env bash
# Read-only snapshot for a desktop stall. Run it from a terminal opened with Ctrl+Alt+T while the
# screen is black and the pointer is a cross. Writes ~/work-environment-backup/freeze-snapshots/<time>.txt
set -u
OUT="$HOME/work-environment-backup/freeze-snapshots"; mkdir -p "$OUT"
F="$OUT/$(date +%Y%m%d-%H%M%S).txt"
PID=$(pgrep -x gnome-shell | head -1)
{
  echo "=== snapshot $(date -Is) ==="
  echo "--- does GNOME Shell answer D-Bus (3 s timeout)?"
  if timeout 3 gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
       --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then echo "yes"; else echo "NO RESPONSE"; fi
  echo "--- gnome-shell pid: ${PID:-none}"
  [ -n "$PID" ] && ps -o pid,stat,pcpu,etime,args -p "$PID"
  echo "--- gnome-shell threads (state, wait channel)"
  [ -n "$PID" ] && for t in /proc/$PID/task/*; do
    printf '%s %s %s\n' "$(basename "$t")" "$(awk '{print $3}' "$t/stat" 2>/dev/null)" "$(cat "$t/wchan" 2>/dev/null)"
  done | sort | uniq -c -f1 | head -40
  echo "--- processes using CPU or stuck on I/O"
  ps -eo pid,stat,pcpu,comm --sort=-pcpu | head -12
  echo "--- pressure (CPU / memory / I/O)"
  for p in cpu memory io; do echo "$p: $(head -1 /proc/pressure/$p 2>/dev/null)"; done
  echo "--- last 40 gnome-shell log lines"
  journalctl --user -b 0 --no-pager -n 40 _COMM=gnome-shell 2>/dev/null | cut -c1-200
  echo "--- backtrace (if gdb is installed)"
  if [ -n "$PID" ] && command -v gdb >/dev/null; then timeout 60 gdb -p "$PID" -batch -ex "thread apply all bt 12" 2>/dev/null | head -150; else echo "gdb not installed"; fi
} > "$F" 2>&1
echo "saved $F"
