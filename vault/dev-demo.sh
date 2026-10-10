#!/usr/bin/env bash
# See the quiz window without installing anything: starts a throwaway daemon in a temp directory (as you, no sudo),
# opens the PRACTICE quiz, and cleans up when you close it. Nothing real is read or changed.
#
#   ./vault/dev-demo.sh [--tier launch|remove_app|change_settings] [--bank DIR]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIER=launch
BANK="$HOME/.local/share/wenv-vault/bank"
while [ $# -gt 0 ]; do
  case "$1" in
    --tier) TIER="$2"; shift 2 ;;
    --bank) BANK="$2"; shift 2 ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[ -d "$BANK" ] && ls "$BANK"/*.jsonl >/dev/null 2>&1 || {
  echo "no question bank at $BANK. Build one: ./vault/fetch_bank.sh DIR, then use --bank DIR/bank" >&2; exit 1; }

T="$(mktemp -d "${TMPDIR:-/tmp}/wenv-vault-demo.XXXXXX")"
DAEMON=""
cleanup() {
  [ -z "$DAEMON" ] || kill "$DAEMON" 2>/dev/null || true
  wait 2>/dev/null || true
  rm -rf "$T"
}
trap cleanup EXIT

mkdir -p "$T/var/lib/wenv-vault" "$T/etc/wenv-vault"
cp -r "$BANK" "$T/var/lib/wenv-vault/bank"
python3 -I - "$HERE" "$T/etc/wenv-vault/policy.json" <<'PY'
import os, sys
sys.path.insert(0, sys.argv[1])
import config
config.save_policy(sys.argv[2], config.default_policy(os.getuid()))
PY

# A Monday, so the weekday rules apply whatever day it really is.
WENV_VAULT_NOW="${WENV_VAULT_NOW:-2026-10-12T10:00:00}" \
  python3 -I "$HERE/vaultd.py" --root "$T" --socket "$T/s.sock" >/dev/null 2>"$T/daemon.err" &
DAEMON=$!
for _ in $(seq 1 50); do [ -S "$T/s.sock" ] && break; sleep 0.1; done
[ -S "$T/s.sock" ] || { echo "the demo daemon did not start:"; cat "$T/daemon.err"; exit 1; }

echo "Practice quiz ($TIER). Pass or close the window; nothing is changed."
WENV_VAULT_SOCKET="$T/s.sock" WENV_VAULT_DRAFTS="$T/drafts" \
  python3 -I "$HERE/gate.py" --practice --tier "$TIER" && echo "passed" || echo "not passed (that is fine for a demo)"
