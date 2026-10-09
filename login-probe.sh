#!/usr/bin/env bash
# Read-only login probe (runs once per log-in from autostart, for 5 minutes, every second).
# Records the facts needed to explain a stuck log-in, without anyone having to act during it:
#   shell  = does GNOME Shell answer D-Bus within 1 s
#   cpu    = GNOME Shell CPU use over the last second (busy loop vs waiting)
#   state  = kernel state of the Shell main thread (R running, S sleeping, D blocked on I/O)
#   serial = display-configuration serial (goes up each time monitors/scale are reconfigured)
#   scale  = active scale, wins = mapped top-level X windows, anim = enable-animations
LOG="$HOME/work-environment-backup/login-probe.log"
echo "=== probe start $(date -Is) session=${XDG_SESSION_TYPE:-?} ===" >> "$LOG"
end=$(( $(date +%s) + 300 )); prev=""
hz=$(getconf CLK_TCK)
while [ "$(date +%s)" -lt "$end" ]; do
  t=$(date '+%H:%M:%S')
  pid=$(pgrep -x gnome-shell | head -1)
  if [ -n "$pid" ]; then
    ticks=$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null); st=$(awk '{print $3}' /proc/$pid/stat 2>/dev/null)
    cpu=$([ -n "$prev" ] && echo $(( (ticks - prev) * 100 / hz ))% || echo "-"); prev=$ticks
  else cpu="no-shell"; st="-"; fi
  if timeout 1 gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell \
       --method org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then shell=ok; else shell=NO; fi
  dc=$(timeout 1 gdbus call --session --dest org.gnome.Mutter.DisplayConfig --object-path /org/gnome/Mutter/DisplayConfig \
       --method org.gnome.Mutter.DisplayConfig.GetCurrentState 2>/dev/null)
  serial=$(printf '%s' "$dc" | grep -o '^(uint32 [0-9]*' | grep -o '[0-9]*$')
  scale=$(printf '%s' "$dc" | python3 -c 'import sys,re; m=re.findall(r"\((-?\d+), (-?\d+), ([0-9.]+), uint32 \d+, (?:true|false), ", sys.stdin.read()); print(m[0][2] if m else "-")' 2>/dev/null)
  wins=$(xwininfo -root -children 2>/dev/null | grep -c '^ *0x')
  anim=$(timeout 1 gsettings get org.gnome.desktop.interface enable-animations 2>/dev/null)
  echo "$t shell=$shell cpu=$cpu state=$st serial=${serial:-?} scale=${scale:-?} wins=$wins anim=${anim:-?}" >> "$LOG"
  sleep 1
done
echo "=== probe end $(date -Is) ===" >> "$LOG"
