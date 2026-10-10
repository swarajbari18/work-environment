#!/usr/bin/env bash
# Remove your user from the docker group, so no process running as you can reach root through the docker socket
# without a password. Needs root.
#
#   sudo ./vault/docker-off.sh [--yes] [--user NAME] [--dry-run]
#
# Existing images, volumes and containers are untouched. Takes effect at your next log-in; until then every
# process already running (including an open agent session) still has the group.
set -euo pipefail

USER_NAME="${SUDO_USER:-}" YES=0 DRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --user) USER_NAME="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
die() { echo "docker-off: $*" >&2; exit 1; }
[ -n "$USER_NAME" ] || die "cannot tell which user; run through sudo or pass --user NAME"
[ "$USER_NAME" != root ] || die "refusing to touch root"
[ "$DRY" -eq 1 ] || [ -n "${WENV_GPASSWD:-}" ] || [ "$(id -u)" -eq 0 ] || die "needs root: sudo $0"

GROUPS_OF="${WENV_FAKE_GROUPS:-$(id -nG "$USER_NAME" 2>/dev/null || true)}"
case " $GROUPS_OF " in
  *" docker "*) ;;
  *) echo "docker-off: $USER_NAME is not in the docker group; nothing to do."; exit 0 ;;
esac

cat <<INFO
This removes $USER_NAME from the docker group.

What changes:
  - 'docker ...' fails for you and for any agent; use 'sudo docker ...' (password cached about 15 minutes)
  - tools that call docker non-interactively (IDE plugins, scripts, Makefiles) will fail until run with sudo
  - files that containers write into bind mounts are owned by root
What does not:
  - your images, volumes and containers stay exactly as they are
Undo any time:  sudo usermod -aG docker $USER_NAME   (then log out and in)
INFO

if [ "$DRY" -eq 1 ]; then echo "(dry run: nothing changed)"; exit 0; fi
if [ "$YES" -ne 1 ]; then
  printf "Type 'yes' to continue: "
  read -r answer || answer=""
  [ "$answer" = yes ] || { echo "cancelled; nothing changed"; exit 1; }
fi

"${WENV_GPASSWD:-gpasswd}" -d "$USER_NAME" docker
echo "done. Log out and back in for it to take effect, then check:  id -nG   (no 'docker')"
if [ -n "${SUDO_USER:-}" ]; then rm -f "/run/sudo/ts/$SUDO_USER" 2>/dev/null || true; fi
