#!/usr/bin/env python3
"""wenv-launch NAME [--] COMMAND...  : run COMMAND, after the quiz if the vault is locked.

Used as the Exec of vault apps' desktop entries. Fails open: if the daemon cannot be reached the app just starts.
Returns 1 (and starts nothing) when the quiz window is closed without passing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import client as vclient  # noqa: E402


def parse(argv):
    args = list(argv)
    if not args:
        raise SystemExit("usage: wenv-launch NAME [--] COMMAND...")
    name = args.pop(0)
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        raise SystemExit("wenv-launch: no command to run")
    return name, args


def decide(vault):
    """'run' or 'gate'. Anything unexpected means run."""
    try:
        status = vault.call("status")
    except vclient.VaultUnavailable:
        return "run"
    if status.get("ok") and status.get("gate_required"):
        return "gate"
    return "run"


def main(argv=None, vault=None, gate_runner=None, exec_fn=os.execvp):
    name, command = parse(sys.argv[1:] if argv is None else argv)
    vault = vault or vclient.VaultClient()
    if decide(vault) == "gate":
        if gate_runner is None:
            from gate import run_gate
            gate_runner = run_gate
        if not gate_runner(vault, "launch", app_label=name, auto_close=1.0):
            return 1
    exec_fn(command[0], command)
    return 0                                      # only reached by a fake exec in tests


if __name__ == "__main__":
    sys.exit(main())
